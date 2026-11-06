# Reverse-engineering toolchain: provider requirements and repairs

Recorded 2026-10-27. This file covers the local tools used to take the
vendor binary `bin/logicport_2371.exe` apart. It is not protocol content.

## Provider state

REA runs as an MCP server: `npx -y rea-agents@<version> mcp`. Two
environment values decide which providers work:

| Variable | Value |
| --- | --- |
| `GHIDRA_INSTALL_DIR` | `/opt/homebrew/opt/ghidra@12.1.4/libexec` |
| `HOPPER_LAUNCHER_PATH` | `~/Applications/Hopper Disassembler.app/Contents/MacOS/hopper` |

| Provider | State | Note |
| --- | --- | --- |
| Ghidra | working | Admission accepts the 12.1.x line only. The verified build is 12.1.4. |
| Hopper | installed | The provider is listed as available. A stale document handle can block every later open. |
| IDA | not configured | No local install. |

## Failure 1: Ghidra version gate

Symptom. `open_binary` refuses with `unsupported_version` and the
diagnostic "accepted releases are 12.1.x (verified build 12.1.4)". The
Homebrew formula had moved to 12.2.

Cause. REA admits the 12.1.x line by design. This is not a stale
allow-list: the 6.8.0 testing guide still states "provider admission
accepts Ghidra 12.1.x". Updating REA does not lift the gate.

Repair. Install 12.1.4 beside 12.2 and point the variable at it.

```
export HOMEBREW_NO_INSTALL_FROM_API=1
brew tap-new <user>/ghidra
brew extract --version=12.1.4 ghidra <user>/ghidra
brew install <user>/ghidra/ghidra@12.1.4
```

The extract reads the formula's own commit history. Two traps were hit.
First, `brew install ghidra@12.1.4` does not work: a versioned formula
exists only when one is published. Second, the extract fails with "No
available formula" if it runs in the same command as the tap clone,
before the tap is registered. Run the extract as its own command.

## Failure 2: Hopper cleanup wedge

Symptom. Every `open_binary` call fails with `cleanup_incomplete` and
"authenticated shutdown acknowledgement was not observed", even with no
Hopper process running.

Cause. The REA server keeps the failed document handle in process
memory. The state survives process exits on the Hopper side.

Repair. Restart the REA server. The server processes are
`npm exec rea-agents@<version> mcp`. A plain `kill` can leave a child
alive; `pkill -9 -f "rea-agents@<old version>"` clears them. The client
starts a fresh server on the next tool call.

## Target identity: two files, one program

`bin/logicport_2371.exe` is the **installer**, not the application. It is
4.9 MB, but its `.text` is 4 KB and Ghidra finds 75 procedures and 90
strings in it. Every address in `docs/freq-decode-lane.md` lands
outside its mapped segments and the provider answers `outside-memory`.

The application is `extracted/files/LogicPort.exe`. It is 1.72 MB, and
the lane's address space fits inside it (`0x400000` plus the image size
reaches about `0x5A0000`, which covers `0x4c8541`, `0x4f2dd4` and the
waveform tables at `0x5962a4`). Open that file for any lane work.

Recorded identities:

| File | SHA-256 |
| --- | --- |
| `extracted/files/LogicPort.exe` | `343745f3300c77a7c3c28b370c1ed34416810ec000aa5fd2d79cfe73e3aaa868` |
| `bin/logicport_2371.exe` (installer) | `4943568f001a481250dd73ce9d841de73b274c0f93d0897f3c7adc222f811d0c` |

## Order of operations

1. Set the version pin and the install paths in `~/.claude.json`
   (`mcpServers.rea`).
2. Restart the server processes.
3. Call a provider tool. A version refusal returns at once; a working
   provider begins analysis and can run for minutes on a multi-megabyte
   PE.
