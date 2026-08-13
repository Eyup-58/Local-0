Local Zero
==========

A local AI assistant for Windows. It runs on your machine, and the parts that could send anything
off it are the parts you switch on yourself.


Install
-------

1. Install uv, if you do not have it:

       winget install --id=astral-sh.uv -e

   uv fetches the Python version this needs by itself. Nothing else has to be installed first -
   the system sidecar is self-contained and needs no .NET runtime.

2. Double-click install.cmd, or run:

       powershell -ExecutionPolicy Bypass -File install.ps1

   It installs for you only, under %LOCALAPPDATA%\Programs\LocalZero. It never asks for
   administrator, and it refuses to run elevated.

3. Start Menu -> Local Zero. A browser tab opens at http://127.0.0.1:8765.


For the local model
-------------------

Local Zero answers with a model on your own machine. Install Ollama from https://ollama.com, then:

    ollama pull qwen2.5:14b
    ollama pull nomic-embed-text

Without these it still starts, still shows telemetry, and says what is missing rather than
pretending. It never sends anything to a cloud model unless you switch it on and provide a key.


Where your data lives
---------------------

    %LOCALAPPDATA%\LocalZero\workspace      files capabilities may read and write
    %LOCALAPPDATA%\LocalZero\trust.json     whether untrusted content may propose actions
    %LOCALAPPDATA%\LocalZero\provider.json  which model layer is selected
    %LOCALAPPDATA%\LocalZero\logs           the audit log: every decision, denials included
    %LOCALAPPDATA%\LocalZero\memory.sqlite  the vault index

None of it is inside the program directory, so an uninstall does not take it with the binaries.


Uninstall
---------

    powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Programs\LocalZero\uninstall.ps1"

That removes the program and keeps your data. Add -RemoveData to remove the list above as well,
and -RemoveStoredKey to remove a stored cloud API key from Credential Manager. Your Obsidian vault
is never touched.
