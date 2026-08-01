import sys
import threading
import socket
from schemas import *
import logging
import os
import sys

# Track two levels up from client.py to find the project root
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from shared.util.logger_util import setup_app_logging

setup_app_logging(__file__)


def receive(conn, addr):
    try:
        message = conn.recv(65536).decode()
        valid = False
        if len(message) > 65535:
            conn.sendall(b'Payload too large\n')
        elif not message:
            conn.sendall(b'Invalid message\n')
        else:
            valid = True
        if not valid:
            return
        # parse the schema from here, i.e. json string to PDU object
        # ....
        # then execute the appropriate action depending on the schema
        # create a def for that action as needed
        # ....
    finally:
        conn.close()

PORT = 4444
listening = False
server_socket = socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.bind(('', PORT))

try:
    server_socket.listen()
    listening = True
    print(f'Server started at localhost on port ${PORT}')
except socket.error:
    print('Unable to start server')

try:
    while listening:
        conn, addr = server_socket.accept()
        thread = threading.Thread(target = receive, args = (conn, addr))
        thread.start()
except KeyboardInterrupt:
    print('Server stopped via Ctrl+C')
    server_socket.close()
    sys.exit()
