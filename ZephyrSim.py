#!/usr/bin/env python3
"""
ZephyrSim_Main.py
This script simulates the Zephyr communications with a StratoCore system. It sets up the necessary file structure, 
opens serial ports for communication, and starts the main output window for the ZephyrSim simulator. It also listens for 
instrument messages over serial and handles command responses.
Modules:
    ZephyrSimGUI
    SerialProcessor
    ZephyrSimUtils
    argparse
    os
Functions:
    FileSetup() -> dict:
        Sets up the file structure and creates necessary files for the session.
    parse_args() -> argparse.Namespace:
        Parses command-line arguments.
    main() -> None:
        Main function that initializes the ZephyrSim simulator, sets up file structure, opens serial ports, and starts 
        the main output window. It also listens for instrument messages and handles command responses.
"""
# -*- coding: utf-8 -*-

from PyQt6 import QtGui, QtWidgets

import gc
import resource
import sys
import tracemalloc

import ZephyrSignals
import ZephyrSimResources_rc  # noqa: F401
if sys.version_info < (3, 9):
    raise Exception("This script requires Python 3.9 or later. Please upgrade Python.")

# modules
import ConfigDialog
import ZephyrSimGUI
import SerialProcessor
import ZephyrSimUtils
import os
import argparse
import datetime

# libraries
import tracemalloc, gc, resource, time

def FileSetup(config:dict) -> dict:
    '''
    Docstring for FileSetup
    
    :param config: Configuration dictionary containing session parameters.
    :type config: dict
    :return: Dictionary containing paths to created files and directories.
    :rtype: dict[str, str]
    '''

    # create date and time strings for file creation
    date, start_time, start_time_file, _ = SerialProcessor.GetDateTime()

    # create the output directory structure for the session
    data_dir = config['DataDirectory']+'/'
    if not os.path.exists(data_dir):
        os.mkdir(data_dir)
    output_dir = data_dir + config['Instrument'] + "_" + date + "T" + start_time_file
    os.mkdir(output_dir)

    # create a directory for individual TM messages
    tm_dir = output_dir + '/TM'
    os.mkdir(tm_dir)

    # create instrument output and command filenames
    inst_filename = output_dir + "/" + config['Instrument'] + "_DBG_" + date + "T" + start_time_file + ".txt"
    xml_filename  = output_dir + "/" + config['Instrument'] + "_XML_" + date + "T" + start_time_file + ".txt"
    cmd_filename  = output_dir + "/" + config['Instrument'] + "_CMD_" + date + "T" + start_time_file + ".txt"

    # create the files
    with open(inst_filename, "w") as inst:
        inst.write(config['Instrument'] + " Debug Messages: " + date + " at " + start_time + "\n\n")

    with open(xml_filename, "w") as inst:
        inst.write(config['Instrument'] + " XML Messages: " + date + " at " + start_time + "\n\n")

    with open(cmd_filename, "w") as inst:
        inst.write(config['Instrument'] + " Commands: " + date + " at " + start_time + "\n\n")

    return {
        "output_dir": output_dir,
        "tm_dir": tm_dir,
        "inst_filename": inst_filename,
        "xml_filename": xml_filename,
        "cmd_filename": cmd_filename,
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='ZephyrSim_Simulator',
        description='Simulates the Zephyr communications with a StratoCore system.',
        epilog='The Zephyr and Log ports may be separate or shared, depending on the StratoCore system configuration.')
    args = parser.parse_args()
    return args

def main() -> None:

    parse_args()

    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(":/icons/icon.svg"))

    while True:
        dialog = ConfigDialog.ConfigDialog()
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.result_config is not None:
            config = dialog.result_config
            break
        else:
            sys.exit(0)

    # set up the files and directories for the session
    file_paths = FileSetup(config)

    # create the signal bus for communication between the serial processor and the GUI
    app_signals = ZephyrSignals.ZephyrSignalBus()

    # Create the main output window
    gui_manager = ZephyrSimGUI.ZephyrSimGUI(
        app_signals,
        config,
        logport=config['LogPort'],
        zephyrport=config['ZephyrPort'],
        cmd_fname=file_paths["cmd_filename"],
    )

    # Set the tm filename for display in the GUI and for writing individual 
    # TM messages to the correct directory
    gui_manager.set_tm_dir(file_paths["tm_dir"])

    # start listening for instrument messages over serial via readyRead signals.
    # Set parent to the GUI manager window so that the object will have Qt lifetime 
    # management and will be properly cleaned up when the GUI is closed.
    SerialProcessor.SerialProcessor(
        app_signals=app_signals,
        logport=config['LogPort'],
        zephyrport=config['ZephyrPort'],
        inst_filename=file_paths["inst_filename"],
        xml_filename=file_paths["xml_filename"],
        tm_dir=file_paths["tm_dir"],
        instrument=config['Instrument'],
        shared_ports=config['SharedPorts'],
        parent=gui_manager.window)

    sys.exit(app.exec())

if (__name__ == '__main__'):
    main()
