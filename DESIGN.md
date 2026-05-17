# Design

The server is designed to host personal assistants for multiple users on a machine (MacMini) located in the household.

Initially all user interaction will be through a locally hosted web interface (see below). Later other interaction methods through messaging services and/or voice will be added.

The system hosts two types of agents. Personal Agents (PA) and Tool Agents (TA). 

Users always interact with a Personal Agent (PA), which uses a locally hosted model. The user's PA can ask other agents questions in English, and receive answers, also in English.

Tool Agents are used to perform specialized tasks, which may involve system calls, executables, or calling out to external services.


## Personal Agent

Each user has a Personal Agent (PA). The PA is given a name and a folder where it can store configuration (json), session state, logs, and memory files.

The user can ask its PA to perform a wide variety of tasks. The user can maintain multiple simultaneous chat sessions with the PA.

A PA interprets incoming messages using a local model, the output of the model instructs following actions.


### Short Term Memory

Each PA session will maintain a short term memory file of current interactions. This short term memory is automatically updated at each turn only with relevant details that can be useful later.

The PA is instructed to generate a note each time something significant has happened that needs to be remembered. Notes are in the following form:

```
<<NOTE:
brief one line statement with pertinent information
>>
```

These notes are intercepted in the agent's stream and are appended to the short term memory file.

An example of a note after booking a car:

```
...
Ok, the car is booked and will arrive 5:15pm.
<<NOTE:
Booked Uber, 5:15pm, home to the party in Los Gatos.
>>
```

### Long Term Memory

At the completion or abandonment of a task the short term memory is automatically summarized and relevant information is added to a long term memory file that is shared by all PA sessions.

The message handler automatically takes care of updating long and short term memory.

The configuration contains a long term memory file trigger size. Once that size is reached long term memory will be compacted automatically. A local model is used for compaction.


### Use of Long Term Memory

At the start of a PA session, all of the long term memory is inserted into the stream using an FYI tag:

```
<<FYI:
memory items to be used by the agent as needed 
>>
```

The agent is instructed that it can use any information in the FYI tag, but that it should otherwise ignore it.

When long term memory is updated, all active sessions for that PA will receive an FYI tag with the updated memory contents.


## Web Interface

Initially the main way to interact with a PA is through a web interface. 

The web interface is light colored and has a session bar on the left. On the right is a chat window, with an input box at the bottom.

The PA's name and the active model are displayed as the title of the chat.

Each message has a type, each type can be hidden, shown, or collapsed. Each message is displayed in a bubble. User messages are right aligned.

Messages may contain markdown, which is displayed inside the bubble accordingly.

The input box allows for multi-line input using ctrl-enter. Enter sends.

It will be possible to display/post other media types such as images/audio/video. Initially only images will be supported.


## Web UI Wire Protocol

The web UI communicates with the server via a single WebSocket connection per PA session.

```
Connect: ws://<server>/ws/<pa-name>/<session-id>
```

The WebSocket provides bidirectional real-time communication. The client sends user messages; the server streams back responses, tool calls, thinking, errors, and system events.

### Message Format

All messages are JSON. The `type` field determines the semantics.
This list is not the complete list and will need to be extended to achieve full functionality.

**Client → Server:**

```json
{ "type": "message", "text": "user input" }
{ "type": "create_session", "title": "new chat", "session_id": "session-1"}
{ "type": "select_session", "session_id": "session-1" }
{ "type": "close_session", "session_id": "session-1" }
{ "type": "session_list" }
```

**Server → Client:**

```json
{ "type": "message",       "role": "user",      "text": "..." }
{ "type": "message",       "role": "assistant",  "text": "...", "partial": true }
{ "type": "message",       "role": "think",      "text": "...", "partial": true }
{ "type": "message",       "role": "note",       "text": "..."}
{ "type": "message",       "role": "fyi",        "text": "..."}
{ "type": "message",       "role": "question",   "name": "clock", "input": "current time" }
{ "type": "message",       "role": "answer",     "name": "clock", "content": "5:30pm" }
{ "type": "message",       "role": "system",     "text": "..." }
{ "type": "message",       "role": "error",      "text": "..." }
{ "type": "message",       "role": "image",      "data": "base64..." }
{ "type": "session_list",  "sessions": [ ... ] }
```

The `partial` flag on assistant/think messages signals streaming chunks. The final chunk (or the only chunk for non-streaming messages) omits `partial` or sets it to `false`.

### Role-UI Mapping

| Role | Bubble alignment | Visibility |
|------|-----------------|------------|
| user | Right | Always shown |
| assistant | Left | Always shown |
| system | Left | Always shown |
| image | Inline | Always shown |
| error | Left | Collapsed by default, first line always displayed |
| think | Left | Collapsed by default (`/think on|off|hide|show`) |
| note | Left | Note message, collapsed by default |
| fyi | Left | FYI message, collapsed by default |
| question | Left | Question message, collapsed by default, target name displayed |
| answer | Left | Answer message, collapsed by default, target name displayed |

This maps directly onto the existing message type system (user, note, fyi, question, answer, think, system, error, image). The voice, audio, and video types are reserved for future implementation.


## Tool Agents

There will be a large number of Tool Agents (TA). Each TA provides a natural language interface to one or more tools.

Initially there is a "help" TA, which a PA can use to discover other tools. 

A PA is instructed in the system prompt to use TAs whenever possible, starting with the "help" TA.

Each TA has its own directory with implementation code and configuration files, ```system.txt``` etc.

Tool agents have no memory and are typically implemented using a smaller local model. The model is used to interpret questions from a PA, and generate a tool call. The model is used to format the answer.


### Invocation

To invoke a Tool Agent a Personal Agent is instructed in ```system.txt``` to insert text in the following format:

```
<<Q: name
concise and direct question in english
>>
```

This message is intercepted and routed to the appropriate Tool Agent, the tool agent replies with:

```
<<A: name
concise and direct answer in english
>>
``` 

For example:

```
What is the time?
<<Q: help
How do I get the current time?
>>
<<A: help
Use the 'clock' agent.
>>
<<Q: clock
What is the current time?
>>
<<A: clock
Thu 5/16/2026 16:50 PST
>>
The current time is 4:50pm.
```

### Handling

When a `<<Q>>` is detected in the stream, a session for that agent is started (if not already active), and the question is added into its message queue.

The stream is automatically split into separate message blocks when tags are detected in the stream.

The TA will handle the message asynchronously, and the PA does not block to wait for the answer.

When the TA generates an answer in a `<<A>>`, the answer is added to the PA's message queue and is fed to the model as a user-role message. The system prompt instructs the model to treat it as a TA response rather than human input. This allows the model to use the answer to decide what to do next.

## File Structure

The system supports multiple Personal and Tool agents. We will use the following file structure. 

```
README.md
DESIGN.md
server.sh              -- server starter script
server.env             -- server settings, api keys, etc.
config.json            -- default config
system.txt             -- top-level system prompt
user/
   PA-1/               -- PA for a user
     system.txt        -- agent specific system prompt
     config.json       -- config overrides
     memory.txt        -- long term memory
     log.txt           -- combined logs
     session-1/        -- active PA session
        memory.txt     -- short term memory
        log.txt        -- session log
        state.json     -- session state
        ...
     session-2/
        ...
     tools/            -- user's TA configurations
        gmail.json
        ...
   PA-2/               -- PA for second user
     ...
   ...
src/                   -- source directory
   server.py           -- server implementation
   ...
   agents/
	   personal/        -- PA implementation
	      system.txt
	      agent.py
	      ...
	   help/            -- help TA implementation
	      ...
	   clock/
	      ...
	   gmail/
	      ...
	   ...
www/
   index.html
   index.css
   index.js
   ...
   
```

Each agent folder contains the code and resources needed for its implementation.

User folders contain user specific files, current state, memory, agent configs, overrides, etc. 

User folders are dynamically created when a new PA is created. 


## Implementation Details

### Config

Config files use JSON deep-merge. The final config is built by applying overrides in this order:

1. Top-level `config.json` (defaults)
2. Agent's `config.json` in `agents/<name>/`
3. User's `config.json` in `user/<PA>/`

Later values override earlier ones for any conflicting key.

### System Prompt

The system prompt for an agent is constructed by concatenating the top-level, agent-level, and user-level ```system.txt``` files, in order. The top-level prompt provides the base instructions, the agent-level prompt adds agent-specific behavior, and the user-level prompt adds user-specific preferences.

### Model Selection

The model is configured at the PA level via ```config.json```. The model field contains a backend-prefixed name, e.g. ```omlx:Qwen3.6-35B-A3B-UD-MLX-4bit```. A single PA has one active model at a time. Tool agents configure their own model in their own ```config.json``` (see TA sessions).


### Concurrency

Messages are asynchronous, and are handled in FIFO order. Messages to TAs are asynchronous. This means that the answer messages from multiple calls TAs may be inserted in the message queue out of order.


### Logging

All interactions, tool calls, errors, etc. will be logged for debugging.


### Errors

All errors are displayed and logged, but they are initially collapsed for brevity. The user can open them for more information. 


### Authentication

For now we will assume that the user interacting with a PA is the PA's user. We will address proper authentication later.


### Creating PAs

When the server is started, and no PAs exist, the user will be asked to provide the PA's name and the user's name, and a new PA will be created.


### Initial TAs

* **help** - guide PAs to available TAs
* **clock** - answer questions about dates and time


### PA and TA sessions

When a PA initiates a question to a TA, a session for that TA is created. The TA will specify in its ```config.json``` which model to use. 

The TA session will handle the question and the TA may use a tool call to get the requested information or perform an action.

Any user specific configuration for the TA is stored in the PA's ```tools``` folder. This may contain preferences, history, passwords, credentials, etc.

When a PA session is closed, all the associated TA sessions are closed, if any.

TA sessions will be visible in the web UI, so that they can be examined by the user.

### Agent Code

Agent code lives in the ```agents/``` directory under a sub-directory named after the agent (e.g. ```agents/personal/```, ```agents/clock/```). When the PA needs to invoke a Tool Agent, it constructs a ```<<Q: name ... >>``` message and the server routes it to the TA's code. The server executes the TA code, collects the result, and returns it as a ```<<A: name ... >>``` message.

The Personal Agent code in ```agents/personal/``` handles all PA logic: loading the system prompt, managing sessions, calling the model, handling memory, and routing tool invocations.


### Placeholder TAs

In order to experiment with new TAs, it should be possible to create a placeholder TA. In this case, the user will open the TA session in the web UI and answer the question for the TA.


### System Commands

The following system commands are supported:

* ```/think [on|off|hide|show]``` - control thinking, default on
* ```/status``` - display status such as model/agent/etc.
* ```/time``` - for the last message display time to first token, tokens per second, number of tokens, time to complete
* ```/memory [short|long]``` - display memory contents
* ```/new``` - close this session and start a new one from scratch
* ```/save``` - create a human readable transcript of the current session, including TA calls, thinking etc. for later review. Display as a new tab in the browser.


### Model Backend

Local and remote model backends will be supported.

* ```omlx``` - preferred for fast local models
* ```ollama``` - local and remote models
* ```openai``` - remote models

### Config.json

This will contain:

* ```port``` - 8080
* ```model``` - ```omlx:Qwen3.6-35B-A3B-UD-MLX-4bit```
* ```thinking``` - true
* ```mlx_url``` - ```http://localhost:8000/v1/chat/completions```
* ```memory_compaction_trigger_size``` - 4096


### Server Startup

Server is started using the ```server.sh``` script. It loads any environment variables such as API keys from ```server.env```.

When the server is restarted, PA sessions are recreated by injecting the session's log and short-term memory as FYI tags. TA sessions are not recreated.

## Example Session

```
                 (USER) What is the weather?
(PA)
<<Q: help
How do I get the weather?
>>
<<A: help
You can use the "weather" agent.
>>
<<Q: weather
What is the weather?
>>
<<A: weather
Please specify a location.
>>
What location do you mean?
                 (USER) San Francisco.
<<Q: weather
What is the weather in San Francisco.
>>
<<A: weather
Initially foggy, later sunny and dry. 75F.
>>
At first foggy, but later sunny and dry. About 24C.
                 (USER) Thanks

```

The short term memory item that is created might be:

```
There is a "weather" agent.
Reported the weather in San Francisco.
```
