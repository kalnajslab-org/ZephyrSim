#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt6 import QtCore

# This module defines the ZephyrSignalBus class, which 
# provides a set of PyQt signals for communication between 
# the ZephyrSimProcess and the ZephyrSimGUI.
class ZephyrSignalBus(QtCore.QObject):
    log_message = QtCore.pyqtSignal(str)
    zephyr_message = QtCore.pyqtSignal(str)
    command_message = QtCore.pyqtSignal(str)
