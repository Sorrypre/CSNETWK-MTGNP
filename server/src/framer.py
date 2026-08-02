import struct
import socket

HEADER_SIZE = 4
MAX_PAYLOAD_SIZE = 65535

def send_framed_message(sock: socket.socket, payload_bytes: bytes):
    """
    Packs the length of the payload as a 4-byte big-endian integer and 
    sends it over the socket.
    """
    length = len(payload_bytes)
    header = struct.pack("!I", length) # packs the header into big endian !I
    sock.sendall(header + payload_bytes) # then sends it off

def recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """
    Collects all requested bytes properly by reading 
    exactly num_bytes from a TCP stream.
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        """
        sock.recv might not read all bytes at once so 
        will loop through until the total number of bytes is met
        """
        packet = sock.recv(num_bytes - len(buffer)) 
        if not packet:
            return None  # Client disconnected
        buffer.extend(packet)
    return bytes(buffer)

def read_framed_message(sock: socket.socket) -> bytes:
    """
    Executes the payload framing process.
    Reads the 4-byte big-endian integer to determine length, validates bounds, and 
    retrieves the exact JSON payload.

    Returns payload bytes on success, or raises ValueError/ConnectionError on failure.    
    """
    header_bytes = recv_exact(sock, HEADER_SIZE)
    if not header_bytes:
        raise ConnectionError("Client disconnected while reading header")

    # Unpack 4-byte big-endian unsigned integer
    payload_length = struct.unpack("!I", header_bytes)[0]

    # Reject messages exceeding 65,535 bytes per spec
    if payload_length > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload size {payload_length} exceeds limit of {MAX_PAYLOAD_SIZE} bytes")

    payload_bytes = recv_exact(sock, payload_length)
    if payload_bytes is None or len(payload_bytes) < payload_length:
        raise ConnectionError("Client disconnected before full payload was received")

    return payload_bytes