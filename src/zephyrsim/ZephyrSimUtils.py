#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct 14 15:14:45 2019

Quick program to simulate ZephyrSim for repetitive testing.

The test cycle is defined in main.  All comms are logged to the log file.

Command line usage:
    python3 ZephyrSim_sim.py /dev/tty.usbserial ZephyrSim_Sim_Log.txt

@author: kalnajs
"""

import sys
import xml.etree.ElementTree as ET #import XML library
from xml.dom import minidom
from datetime import datetime
from time import sleep
from PyQt6 import QtSerialPort

msg_id_num = 1

def GetTime() -> tuple:
    # create date and time strings
    current_datetime = datetime.now()
    curr_time = str(current_datetime.time().strftime("%H:%M:%S"))
    milliseconds = str(current_datetime.time().strftime("%f"))[:-3]

    return curr_time, milliseconds


def crc16_ccitt(crc: int, data: bytes) -> int:
    msb = crc >> 8
    lsb = crc & 255

    for c in data:
        x = c ^ msb
        x ^= (x >> 4)
        msb = (lsb ^ (x >> 3) ^ (x << 4)) & 255
        lsb = (x ^ (x << 5)) & 255
    return (msb << 8) + lsb


def AddCRC(InputXMLString: str) -> str:
    crc = crc16_ccitt(0x1021,InputXMLString.encode("ASCII"))

    return InputXMLString + '<CRC>' + str(crc) + '</CRC>\n'


def prettify(xmlStr: ET.Element) -> str:
    INDENT = "\t"
    rough_string = ET.tostring(xmlStr)
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent=INDENT)


def sendIM(instrument: str, InstrumentMode: str, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    XML_IM = ET.Element('IM')

    msg_id = ET.SubElement(XML_IM,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    inst_id = ET.SubElement(XML_IM,'Inst')
    inst_id.text = instrument

    mode = ET.SubElement(XML_IM,'Mode')
    mode.text = InstrumentMode

    pretty_string = prettify(XML_IM)
    without_first_line = pretty_string.split("\n",1)[1]

    output = AddCRC(without_first_line)

    port.write(output.encode())

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sending IM\n")

    return output


def sendGPS(zenith: float, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    # The port might be suspended
    if not port.isOpen():
        return

    XML_GPS = ET.Element('GPS')
    msg_id = ET.SubElement(XML_GPS,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    date = ET.SubElement(XML_GPS,'Date')
    date.text = datetime.today().strftime('%Y/%m/%d')

    time = ET.SubElement(XML_GPS,'Time')
    time.text = datetime.today().strftime('%H:%M:%S')
    #time.text = '11:59:00'

    lon = ET.SubElement(XML_GPS,'Lon')
    lon.text = '-105.000000'

    lat = ET.SubElement(XML_GPS,'Lat')
    lat.text = '40.000000'

    alt = ET.SubElement(XML_GPS,'Alt')
    alt.text = '1620.3'

    sza = ET.SubElement(XML_GPS,'SZA')
    sza.text = str(zenith)

    vbat = ET.SubElement(XML_GPS,'VBAT')
    vbat.text = "16.2"

    diff = ET.SubElement(XML_GPS,'Diff')
    diff.text = "0.00453"

    quality = ET.SubElement(XML_GPS,'Quality')
    quality.text = '3'

    pretty_string = prettify(XML_GPS)
    without_first_line = pretty_string.split("\n",1)[1]
    output = AddCRC(without_first_line)

    port.write(output.encode())

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sending GPS, SZA = " + str(zenith) + "\n")

    return output


def sendTC(instrument: str, command: str, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    XML_TC = ET.Element('TC')

    msg_id = ET.SubElement(XML_TC,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    inst_id = ET.SubElement(XML_TC,'Inst')
    inst_id.text = instrument

    length = ET.SubElement(XML_TC,'Length')
    length.text = str(len(command))

    pretty_string = prettify(XML_TC)
    without_first_line = pretty_string.split("\n",1)[1]

    crc = crc16_ccitt(0x1021,command.encode("ASCII"))

    command = 'START' + command
    output = AddCRC(without_first_line)
    output = output + command

    port.write(output.encode())
    port.write(crc.to_bytes(2,byteorder='big',signed=False))
    port.write(b'END')

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sending TC: " + command + "\n")

    return output


def sendSAck(instrument: str, ACK: str, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    XML_TMAck = ET.Element('SAck')

    msg_id = ET.SubElement(XML_TMAck,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    inst_id = ET.SubElement(XML_TMAck,'Inst')
    inst_id.text = instrument

    ack = ET.SubElement(XML_TMAck, 'Ack')
    ack.text = ACK

    pretty_string = prettify(XML_TMAck)
    without_first_line = pretty_string.split("\n",1)[1]
    output = AddCRC(without_first_line)

    port.write(output.encode())

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sending SAck\n")

    return output


def sendRAAck(instrument: str, ACK: str, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    XML_RAAck = ET.Element('RAAck')

    msg_id = ET.SubElement(XML_RAAck,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    inst_id = ET.SubElement(XML_RAAck,'Inst')
    inst_id.text = instrument

    ack = ET.SubElement(XML_RAAck, 'Ack')
    ack.text = ACK

    pretty_string = prettify(XML_RAAck)
    without_first_line = pretty_string.split("\n",1)[1]
    output = AddCRC(without_first_line)

    port.write(output.encode())

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sent RAAck\n")

    return output


def sendTMAck(instrument: str, ACK: str, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    XML_TMAck = ET.Element('TMAck')

    msg_id = ET.SubElement(XML_TMAck,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    inst_id = ET.SubElement(XML_TMAck,'Inst')
    inst_id.text = instrument

    ack = ET.SubElement(XML_TMAck, 'Ack')
    ack.text = ACK

    pretty_string = prettify(XML_TMAck)
    without_first_line = pretty_string.split("\n",1)[1]
    output = AddCRC(without_first_line)

    port.write(output.encode())

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sending TMAck\n")

    return output


def sendSW(instrument: str, filename: str, port: QtSerialPort.QSerialPort) -> str:
    global msg_id_num

    XML_TMAck = ET.Element('SW')

    msg_id = ET.SubElement(XML_TMAck,'Msg')
    msg_id.text = str(msg_id_num)
    msg_id_num += 1

    inst_id = ET.SubElement(XML_TMAck,'Inst')
    inst_id.text = instrument

    pretty_string = prettify(XML_TMAck)
    without_first_line = pretty_string.split("\n",1)[1]
    output = AddCRC(without_first_line)

    port.write(output.encode())

    time, millis = GetTime()
    timestring = '[' + time + '.' + millis + '] '

    with open(filename, mode='a') as output_file:
        output_file.write(timestring)
        output_file.write("Sending SW\n")

    return output


def listenFor(port: str, reply: str, terminator: bytes, time_out: int, filename: str) -> bool:

    print("Listening For: " + reply)
    ser = QtSerialPort.QSerialPort()
    ser.setPortName(port)
    ser.setBaudRate(115200)
    ser.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
    ser.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
    ser.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
    ser.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
    if not ser.open(QtSerialPort.QSerialPort.OpenModeFlag.ReadWrite):
        print(f"Error opening port {port}: {ser.errorString()}")
        return False

    line = bytes()
    deadline = datetime.now().timestamp() + float(time_out)
    while datetime.now().timestamp() < deadline and not line.endswith(terminator):
        if ser.waitForReadyRead(50):
            line += bytes(ser.readAll())
            if len(line) >= 2000:
                break
    ser.close()
    print(str(line))

    if len(line) > 0:
        with open(filename, mode='a') as output_file:
            output_file.write(str(line))
        if reply in str(line):
            return True
        else:
            return False

    return False




def main() -> None:
    #port = sys.argv[1]
    #LogFile = sys.argv[2]

    LogFile = 'ZephyrSim_LPC_test.txt'
    port = '/dev/tty.usbserial'
    instrument = 'LPC'

    sendGPS(45,LogFile, port)

    #listenFor(port,'IMR',b'</CRC>',60,LogFile)  #listen for and IMR for 60 seconds
    sleep(3)
    sendIM(instrument,'FL',LogFile,port)

#    listenFor(port,'IMAck',b'</CRC>',5,LogFile) #listen for the IMAck
#    sleep(1)
#    sendGPS(45,LogFile, port)

#    for x in range(100):
#        sendGPS(x,LogFile,port)
#        print("Sending Redock command")
#        sendTC('142,12,0.1;',LogFile,port)
#        print("Waiting for Redock TC ACK")
#        listenFor(port,'TCAck',b'</CRC>',5,LogFile) #listen for the TCAck
#        print("Waiting for down RA request")
#        listenFor(port,'RA',b'</CRC>',5,LogFile) #listen for the RA
#        sendRAAck('ACK',LogFile,port)
#        print("Waiting for down motion MCB Confirmation" )
#        listenFor(port,'TM',b'END',60,LogFile) #about to move TM
#        sendTMAck('ACK',LogFile,port)
#        print("Waiting for down motion complete MCB TM" )
#        listenFor(port,'TM',b'END',60,LogFile) #down profile mcb tm
#        sendTMAck('ACK',LogFile,port)
#
#        print("Waiting for up RA request")
#        listenFor(port,'RA',b'</CRC>',60,LogFile) #listen for the RA
#        sendRAAck('ACK',LogFile,port)
#        print("Waiting for up motion MCB Confirmation " )
#        listenFor(port,'TM',b'END',60,LogFile) #about to move TM
#        sendTMAck('ACK',LogFile,port)
#        print("Waiting for up MCB docking stall detected TM" )
#        listenFor(port,'TM',b'END',60,LogFile) #motion complete TM
#        print("Waiting for up MCB complete TM" )
#        listenFor(port,'TM',b'END',60,LogFile) #motion complete TM
#        sendTMAck('ACK',LogFile,port)
#        print("Waiting for dock success TM" )
#        if listenFor(port,'PU docked:',b'END',60,LogFile):#PU comms TM
#            dock_success += 1
#            print("Dock Success! Successes: " + str(dock_success) + " Failures: " + str(dock_fails))
#        else:
#            dock_fails += 1
#            print("Dock FAILED! Successes: " + str(dock_success) + " Failures: " + str(dock_fails))
#
#        sendTMAck('ACK',LogFile,port)
#        sleep(1)
#        print("Starting next cycle")



if (__name__ == '__main__'):
    main()






