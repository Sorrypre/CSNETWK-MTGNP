import sys
import threading
import socket
from schemas import *

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
            conn.close()
            return
        # parse the schema from here, i.e. json string to PDU object
        # ....
        # then execute the appropriate action depending on the schema
        # create a def for that action as needed
        # ....

PORT = 4444
listening = False
server_socket = socket(AF_INET, SOCK_STREAM)
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
