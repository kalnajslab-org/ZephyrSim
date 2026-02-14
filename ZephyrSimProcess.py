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
import queue
import datetime
import xmltodict
import time

# globals
zephyr_port = None
log_port = None
inst_filename = ''
xml_filename = ''
inst_queue = None
xml_queue = None
cmd_queue = None
tm_dir = ''
instrument = ''

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

    # place on the queue to be displayed in the GUI
    message = timestring + message
    inst_queue.put(message)

    # log to the file
    with open(inst_filename, 'a') as inst:
        inst.write(message)

def HandleZephyrMessage(first_line: str) -> None:
    next_lines = ''
    while next_lines.find('</CRC>') == -1:
        next_lines = next_lines + zephyr_port.readline().decode('ascii')
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
        cmd_queue.put('TMAck')
    elif 'S' == msg_type:
        cmd_queue.put('SAck')
    elif 'RA' == msg_type:
        cmd_queue.put('RAAck')

    # formulate the time
    _, time, _, milliseconds = GetDateTime()
    timestring = '[' + time + '.' + milliseconds + '] '

    # place on the queue to be displayed in the GUI
    display = f'{timestring} (FROM){msg_dict["XMLTOKEN"]}\n'
    xml_queue.put(display)

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
    inst_queue_in: queue.Queue,
    xml_queue_in: queue.Queue,
    logport: serial.Serial,
    zephyrport: serial.Serial,
    inst_filename_in: str,
    xml_filename_in: str,
    tm_dir_in: str,
    inst_in: str,
    cmd_queue_in: queue.Queue,
    config: dict) -> None:

    global zephyr_port
    global log_port
    global inst_filename
    global xml_filename
    global inst_queue
    global xml_queue
    global tm_dir
    global instrument
    global cmd_queue

    # assign globals
    zephyr_port = zephyrport
    log_port = logport
    inst_filename = inst_filename_in
    xml_filename = xml_filename_in
    inst_queue = inst_queue_in
    xml_queue = xml_queue_in
    tm_dir = tm_dir_in
    instrument = inst_in
    cmd_queue = cmd_queue_in

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
                        HandleStratoLogMessage(str(new_log_line,'ascii'))
                if zephyr_port.is_open: 
                    new_zephyr_line = zephyr_port.readline()
                    if new_zephyr_line:
                        # skip the stratocore serial keepalive messages
                        if new_zephyr_line != b'\n':
                            try:
                                HandleZephyrMessage(str(new_zephyr_line,'ascii'))
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
                                HandleZephyrMessage(str(new_line,'ascii'))
                            # otherwise, it is a log message
                            else:
                                HandleStratoLogMessage(str(new_line,'ascii'))
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
