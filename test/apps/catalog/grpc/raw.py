"""gRPC servers that write HTTP/2 frames themselves, to send what gRPC server libraries refuse to."""

from __future__ import annotations

import socketserver
import threading
from dataclasses import dataclass
from pathlib import Path

from google.protobuf import wrappers_pb2

from test.apps.catalog.grpc.descriptors import users_descriptor_set, write_descriptor_set

PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
DATA, HEADERS, SETTINGS, PING = 0x0, 0x1, 0x4, 0x6
END_STREAM, ACK, END_HEADERS = 0x1, 0x1, 0x4
HEADER_TABLE_SIZE = 0x1
# HPACK dynamic table size before the client sets its own.
DEFAULT_TABLE_SIZE = 4096
# Bytes HPACK adds to the name and value of each dynamic table entry.
ENTRY_OVERHEAD = 32
# Index of the newest entry in the HPACK dynamic table.
NEWEST_ENTRY = 62
STATUS = b"grpc-status"


def hpack_integer(value: int, prefix_bits: int, first: int) -> bytes:
    limit = (1 << prefix_bits) - 1
    if value < limit:
        return bytes([first | value])
    encoded = bytearray([first | limit])
    value -= limit
    while value >= 128:
        encoded.append(value & 0x7F | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def hpack_string(value: bytes) -> bytes:
    return hpack_integer(len(value), 7, 0) + value


# `:status: 200` from the HPACK static table, then `content-type` kept out of the dynamic table.
RESPONSE_HEADERS = b"\x88\x00" + hpack_string(b"content-type") + hpack_string(b"application/grpc")


def frame(kind: int, flags: int, stream: int, payload: bytes) -> bytes:
    return len(payload).to_bytes(3, "big") + bytes([kind, flags]) + stream.to_bytes(4, "big") + payload


class EchoStatus(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.rfile.read(len(PREFACE))
        self.wfile.write(frame(SETTINGS, 0, 0, b""))
        bodies: dict[int, bytes] = {}
        table_size = DEFAULT_TABLE_SIZE
        # Starts the next header block when the client shrinks the table.
        size_update = b""
        status_in_table = False
        while header := self.rfile.read(9):
            kind, flags, stream = header[3], header[4], int.from_bytes(header[5:], "big")
            payload = self.rfile.read(int.from_bytes(header[:3], "big"))
            if kind == SETTINGS and not flags & ACK:
                for offset in range(0, len(payload), 6):
                    limit = int.from_bytes(payload[offset + 2 : offset + 6], "big")
                    if int.from_bytes(payload[offset : offset + 2], "big") == HEADER_TABLE_SIZE and limit < table_size:
                        table_size = limit
                        size_update = hpack_integer(limit, 5, 0x20)
                        status_in_table = False
                self.wfile.write(frame(SETTINGS, ACK, 0, b""))
            elif kind == PING and not flags & ACK:
                self.wfile.write(frame(PING, ACK, 0, payload))
            elif kind == DATA:
                bodies[stream] = bodies.get(stream, b"") + payload
                if flags & END_STREAM:
                    # `pkg.GetUserRequest` carries `user_id` the way `Int32Value` carries `value`.
                    user_id = wrappers_pb2.Int32Value.FromString(bodies.pop(stream)[5:]).value
                    value = str(user_id).encode()
                    # Each status that fits goes into the dynamic table, and later ones reuse the name of the newest
                    # entry, as HPACK encoders do.
                    if len(STATUS) + len(value) + ENTRY_OVERHEAD > table_size:
                        field = b"\x00" + hpack_string(STATUS) + hpack_string(value)
                    elif status_in_table:
                        field = bytes([0x40 | NEWEST_ENTRY]) + hpack_string(value)
                    else:
                        field = b"\x40" + hpack_string(STATUS) + hpack_string(value)
                        status_in_table = True
                    block = size_update + RESPONSE_HEADERS + field
                    size_update = b""
                    self.wfile.write(frame(HEADERS, END_HEADERS | END_STREAM, stream, block))


@dataclass(slots=True)
class RawGrpcServer:
    target: str
    descriptor_set_path: Path
    server: socketserver.ThreadingTCPServer

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def echo_status(directory: Path) -> RawGrpcServer:
    """`pkg.Users/GetUser` answers with the requested `user_id` as `grpc-status`, a negative one included."""
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoStatus)
    server.daemon_threads = True
    # `shutdown` waits up to one poll interval for the loop to stop.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return RawGrpcServer(
        target=f"grpc://127.0.0.1:{server.server_address[1]}",
        descriptor_set_path=write_descriptor_set(directory, users_descriptor_set()),
        server=server,
    )
