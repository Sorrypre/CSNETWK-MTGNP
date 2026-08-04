import socket
import struct
import json
import threading

def recv_loop(sock):
    while True:
        header = sock.recv(4)
        if not header:
            print("Server closed connection.")
            break
        length = struct.unpack("!I", header)[0]
        msg = json.loads(sock.recv(length).decode('utf-8'))
        print(f"\n[SERVER]: {json.dumps(msg, indent=2)}\n> ", end="")

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 4444))

# Start background thread to listen for server messages
threading.Thread(target=recv_loop, args=(s,), daemon=True).start()

print("Connected! Paste JSON and press Enter to send.")
while True:
    try:
        user_input = input("> ")
        if user_input.strip():
            msg_dict = json.loads(user_input)
            payload = json.dumps(msg_dict).encode('utf-8')
            s.sendall(struct.pack("!I", len(payload)) + payload)
    except Exception as e:
        print(f"Invalid input: {e}")