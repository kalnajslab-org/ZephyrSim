# ZephyrSim Simulator

![ZephyrSim Simulator Overview](ZephyrSim_Simulator.png)

This repository contains a platform-independent, Python-based ZephyrSim for the CNES Strateole 2 campaign. This simulator adds the ability to receive and display debug messages from LASP instruments over the same serial connection as the XML-based Zephyr communications.

## Quickstart
```sh
pip3 install -r requirements.txt
python3 ZephyrSim.py
```

The first time that you run it you will get a popup from `SimplePythonGUI` asking if you have a license for non-commericial usage.

## Dependencies

See *requirements.txt* for python modules.

## Interface

The simulator uses the `PySimpleGUI` library to provide multiple input and output windows that allow the user to interact with the instrument under test.

### Startup

On startup, the user has the following options:

<img src="/Screenshots/WelcomeWindow.PNG" alt="Welcome Window Screenshot" width="300"/>

Example ports: (Windows) `COM3`, (Linux) `/dev/ttyUSB0`, (MacOS) `/dev/cu.usbmodem165659901`

If the user responds "Yes" to the "Automatically respond with ACKs?" prompt, then in response to `S`, `RA`, and `TM` XML messages, the simulator will send affirmative `SAck`, `RAAck`, and `TMAck` messages respectively. This is the default option. Otherwise, the user must manually send these commands.

### Sending Commands

To send a command, the user must simply click the corresponding button in the Command Menu window and then complete any follow-up prompt windows as applicable.

<img src="/Screenshots/CommandMenu.PNG" alt="Command Menu Screenshot" width="400"/>

### Simulator Log

The commands that are sent and simulator decisions that are made are displayed in the Debug window. Note that certain commands are color-coded. The exact time of each command is prepended in square brackets.

<img src="/Screenshots/DebugWindow.PNG" alt="Debug Window Screenshot" width="500"/>

### Viewing Instrument Output

The Instrument Output window has two scrolling text outputs: the instrument debug output, and the XML output. The instrument debug displays the StratoCore-specific debug messages, where errors are colored red. The XML output shows a succinct one-line message for each type of XML message received. The exact time of each message is prepended in square brackets.

<img src="/Screenshots/InstrumentOutput.PNG" alt="Instrument Output Screenshot" width="900"/>

## Log File Structure

Each time a ZephyrSim Simulator session is successfully started, a directory under the `sessions/` directory is created. Each session's directory will be named according to the date and instrument: `INST_DD-Mmm-YY_HH-MM-SS/`.

### Session Contents

`INST_CMD_DD-Mmm-YY_HH-MM-SS.txt`: logs all of the commands sent to the instrument

`INST_DBG_DD-Mmm-YY_HH-MM-SS.txt`: logs all of the debug messages received from the instrument

`INST_XML_DD-Mmm-YY_HH-MM-SS.txt`: logs all of the XML messages received from the instrument

`TM`: directory containing individual, timestamped files for each telemetry message in the same format as found on the CCMZ

### Example File Structure

```
sessions/
|---RACHUTS_04-Jun-20_12-04-32/
|   |   RACHUTS_CMD_04-Jun-20_12-04-32.txt
|   |   RACHUTS_DBG_04-Jun-20_12-04-32.txt
|   |   RACHUTS_XML_04-Jun-20_12-04-32.txt
|   |---TM/
|       |    TM_04-Jun-20_12-04-37.RACHUTS.dat
|       |    TM_04-Jun-20_12-04-43.RACHUTS.dat
|---LPC_05-Jun-20_13-07-32/
|   |   LPC_CMD_05-Jun-20_13-07-32.txt
|   |   LPC_DBG_05-Jun-20_13-07-32.txt
|   |   LPC_XML_05-Jun-20_13-07-32.txt
|   |---TM/
|       |    TM_05-Jun-20_13-07-37.LPC.dat
|       |    TM_05-Jun-20_13-07-43.LPC.dat
|---etc...
```

## Scripting

The `Legacy/` directory contains old scripts used to run the original simulator code. The advantage of using scripting is automation. **In the future, this could be achieved with the new architecture by writing out a list of commands with along with timing in a file to be parsed and sent**.
