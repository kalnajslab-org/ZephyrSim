#!/usr/bin/env python3
"""
This module provides functions to handle and process messages from instruments
connected via serial ports. It includes functions to handle log messages, Zephyr
messages, and to write telemetry files. The main function `ReadInstrument` runs
as a thread to continuously read from the serial ports and process the incoming
messages.
Functions:
    GetDateTime() -> tuple:
        Returns the current date and time in various string formats.
    HandleStratoLogMessage(message: str) -> None:
        Processes and logs a Strato log message.
    HandleZephyrMessage(first_line: str) -> None:
        Processes and logs a Zephyr message, and handles specific message types.
    WriteTMFile(message: str, binary: bytes) -> None:
        Writes a telemetry message and its binary payload to a file.
    ReadInstrument(
        Continuously reads from the serial ports and processes incoming messages.
"""
# -*- coding: utf-8 -*-

import serial
import datetime
import xmltodict
import time
from typing import Callable, Optional

import ZephyrSignals

# globals
zephyr_port = None
log_port = None
inst_filename = ''
xml_filename = ''
tm_dir = ''
instrument = ''
signals = None

def GetDateTime() -> tuple:
    # create date and time strings
    current_datetime = datetime.datetime.now()
    date = str(current_datetime.date().strftime("%Y-%m-%d"))
    curr_time = str(current_datetime.time().strftime("%H:%M:%S"))
    curr_time_file = str(current_datetime.time().strftime("%H-%M-%S"))
    milliseconds = str(current_datetime.time().strftime("%f"))[:-3]

    return date, curr_time, curr_time_file, milliseconds

def HandleStratoLogMessage(message: str) -> None:
    message = message.rstrip() + '\n'

    # formulate the time
    _, time, _, milliseconds = GetDateTime()
    timestring = '[' + time + '.' + milliseconds + '] '

    # emit to the GUI thread
    message = timestring + message
    signals.log_message.emit(message)

    # log to the file
    with open(inst_filename, 'a') as inst:
        inst.write(message)

def HandleZephyrMessage(first_line: str) -> None:
    next_lines = ''
    while next_lines.find('</CRC>') == -1:
        next_lines = next_lines + zephyr_port.readline().decode('ascii', errors='ignore')
    message = first_line + next_lines

    # The message is not correct XML, since it doesn't have opening/closing
    # tokens. Add some tokens so that it can be parsed.
    try:
        msg_dict = xmltodict.parse(f'<XMLTOKEN>{message}</XMLTOKEN>')
    except Exception as e:
        # Happens when a garbled message is received, due to the 
        # sleep behavior of the MAX3381 chip. Just ignore the message.
        print('Error parsing XML,', e)
        print('Message:', message)
        return
    msg_type = list(msg_dict["XMLTOKEN"].keys())[0]

    # if TM, save payload
    if 'TM' == msg_type:
        # The binary section contains 'START<bytes><crc>END', where crc is 2 bytes
        n_bytes = 10 + int(msg_dict["XMLTOKEN"]["TM"]["Length"])
        binary_section = bytearray()
        while len(binary_section) < n_bytes:
            binary_section.extend(zephyr_port.read(n_bytes - len(binary_section)))
        WriteTMFile(message, binary_section)
        signals.command_message.emit('TMAck')
    elif 'S' == msg_type:
        signals.command_message.emit('SAck')
    elif 'RA' == msg_type:
        signals.command_message.emit('RAAck')

    # formulate the time
    _, time, _, milliseconds = GetDateTime()
    timestring = '[' + time + '.' + milliseconds + '] '

    # emit to the GUI thread
    display = f'{timestring} (FROM){msg_dict["XMLTOKEN"]}\n'
    signals.zephyr_message.emit(display)

    # log to the file
    with open(xml_filename, 'a') as xml:
        xml.write(display)

def WriteTMFile(message: str, binary: bytes) -> None:
    date, _, time, _ = GetDateTime()

    filename = tm_dir + '/TM_' + date + 'T' + time + '.' + instrument + '.dat'

    with open(filename, 'wb') as tm_file:
        tm_file.write(message.encode())
        tm_file.write(binary)

# This function is run as a thread from ZephyrSim.py.
def ReadInstrument(
    app_signals: ZephyrSignals.ZephyrSignalBus,
    logport: serial.Serial,
    zephyrport: serial.Serial,
    inst_filename_in: str,
    xml_filename_in: str,
    tm_dir_in: str,
    inst_in: str,
    config: dict) -> None:

    global signals
    global zephyr_port
    global log_port
    global inst_filename
    global xml_filename
    global tm_dir
    global instrument

    # assign globals
    signals = app_signals
    zephyr_port = zephyrport
    log_port = logport
    inst_filename = inst_filename_in
    xml_filename = xml_filename_in
    tm_dir = tm_dir_in
    instrument = inst_in

    port_sharing = config['SharedPorts']
    zephyr_port.flushInput()
    if port_sharing:
        log_port.flushInput()

    # main loop
    while True:
        # The zephyr and log ports are opened in ZephyrSimGUI.
        # They can be opened/closed from the GUI, when
        # the suspend button is pressed. Thus the exception
        # handling is used to detect this.
        try:
            if not port_sharing:
                if log_port.is_open: 
                    new_log_line = log_port.readline()
                    if new_log_line:
                        HandleStratoLogMessage(new_log_line.decode('ascii', errors='ignore'))
                if zephyr_port.is_open: 
                    new_zephyr_line = zephyr_port.readline()
                    if new_zephyr_line:
                        # skip the stratocore serial keepalive messages
                        if new_zephyr_line != b'\n':
                            print('New zephyr line:', new_zephyr_line)
                            try:
                                HandleZephyrMessage(new_zephyr_line.decode('ascii', errors='ignore'))
                            except UnicodeDecodeError as e:
                                # Happens when a garbled message is received, due to the 
                                # sleep behavior of the MAX3381 chip. Just ignore the message.
                                print('Error handling Zephyr message, ', e)
                                print('Zephyr line:', new_zephyr_line)
            else:
                # port sharing, so just read from zephyr port
                if zephyr_port.is_open: 
                    new_line = zephyr_port.readline()
                    if new_line:
                        # skip the stratocore serial keepalive messages
                        if new_line != b'\n':
                            # if the line contains a '<', it is a Zephyr message
                            if (-1 != new_line.find(b'<')):
                                HandleZephyrMessage(new_line.decode('ascii', errors='ignore'))
                            # otherwise, it is a log message
                            else:
                                HandleStratoLogMessage(new_line.decode('ascii', errors='ignore'))
        except OSError as e:
            # Happens when the port is closed
            # It's lame to catch this exception at the top level, but seems to be the only way to detect
            # that the port is closed from the gui thread. Should find a way to do this more elegantly.
            print('OSError:', e)
            time.sleep(0.01)
            continue
        except TypeError as e:
            # Happens when the port is closed
            # It's lame to catch this exception at the top level, but seems to be the only way to detect
            # that the port is closed from the gui thread. Should find a way to do this more elegantly.
            time.sleep(0.01)
            continue
