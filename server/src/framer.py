import struct
import socket

HEADER_SIZE = 4
HEADER_FORMAT = "!I"
MAX_PAYLOAD_SIZE = 65535

def recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """
    Reads exact number of bytes or raises ConnectionError/TimeoutError.
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        try:
            chunk = sock.recv(num_bytes - len(buffer))
            if not chunk:
                raise ConnectionError("Client closed connection")
            buffer.extend(chunk)
        except socket.timeout:
            raise TimeoutError("10-second inactivity timeout reached.")
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
    payload_length = struct.unpack(HEADER_FORMAT, header_bytes)[0]

    # Reject messages exceeding 65,535 bytes per spec
    if payload_length > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload size {payload_length} exceeds limit of {MAX_PAYLOAD_SIZE} bytes")

    return recv_exact(sock, payload_length)

def send_framed_message(sock: socket.socket, payload_bytes: bytes) -> None:
    """
    Frames and sends payload with 4-byte big-endian length prefix.
    """
    if len(payload_bytes) > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload exceeds limit of {MAX_PAYLOAD_SIZE} bytes")
    sock.sendall(struct.pack(HEADER_FORMAT, len(payload_bytes)) + payload_bytes)