"""
Copyright (C) 2025 TheFinnaCompany Ltd
Developed by Sofia Avigail Eraslan <sophia.eraslan@thefinna.com>
This program is Internal and Strictly Confidential.

Status: UNSTABLE, ABANDONED PROJECT
"""

"""
Observations by Author:
There is a problem reading the hostname from the packet - despite my best efforts to debug it, I have concluded that this project is not worth pursuing further.
We may return to it in the future.
"""

# Important: (for once stable)
# Do not use this software directly in production.
# It is recommended to deploy HAProxy in front of it and run multiple instances (at least four) on different ports for better load distribution and fault tolerance.
#
# Ensure that a script or cron job is running to periodically check the status of all instances, as they may unexpectedly crash or encounter issues.
#
# When configuring HAProxy, use the ‘leastconn’ load balancing method. This is because the software functions as a proxy, not a traditional server, and maintains a persistent connection to the backend server.
# Also, you may OOM or have too many connections open at once.

import argparse
import json
import socket
import threading
import time
import redis

redis_client = redis.Redis(host='172.31.0.255', port=6379, password='thefinnacompany', decode_responses=True) # Default dev password - switch to env variables later

def read_varint(data, index):
    """Read Minecraft VarInt from byte array"""
    value = 0
    for i in range(5):
        byte = data[index]
        value |= (byte & 0x7F) << (7 * i)
        index += 1
        if not (byte & 0x80):
            break
    return value, index


def extract_hostname(data):
    """Extract hostname from Minecraft handshake packet"""
    index = 0

    # Read packet structure
    packet_length, index = read_varint(data, index)
    packet_id, index = read_varint(data, index)
    protocol_version, index = read_varint(data, index)
    hostname_len, index = read_varint(data, index)

    # Extract hostname string
    hostname_bytes = data[index:index + hostname_len]
    try:
        return hostname_bytes.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError("Invalid hostname encoding")


class ForwardThread(threading.Thread):
    """Handle unidirectional data forwarding"""
    def __init__(self, source, dest):
        super().__init__()
        self.source = source
        self.dest = dest

    def run(self):
        try:
            while True:
                data = self.source.recv(4096)
                if not data:
                    break
                self.dest.send(data)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.source.close()

def drop_connection_after_delay(client_socket, delay):
    """Close the client socket after a delay"""
    time.sleep(delay)
    try:
        client_socket.close()
        print(f"Closed connection due to unknown hostname")
    except:
        pass

def send_motd(client_socket, message):
    response = {
        "version": {"name": "MC-Secure", "protocol": 769},
        "players": {"max": 0, "online": 0},
        "description": {"text": "§6MC-Secure§3§l> "+ message},
    }
    response_json = json.dumps(response)
    response_data = response_json.encode('utf-8')

    # Construct a Minecraft status response packet
    packet_id = b'\x00'
    length = len(response_data)
    length_bytes = b''
    while True:
        temp = length & 0x7F
        length >>= 7
        if length:
            temp |= 0x80
        length_bytes += bytes([temp])
        if not length:
            break

    total_length = len(packet_id + length_bytes + response_data)
    total_length_bytes = b''
    temp_len = total_length
    while True:
        temp = temp_len & 0x7F
        temp_len >>= 7
        if temp_len:
            temp |= 0x80
        total_length_bytes += bytes([temp])
        if not temp_len:
            break

    client_socket.send(total_length_bytes + packet_id + length_bytes + response_data)
    client_socket.close()
    return

def handle_client(client_socket):
    """Main client handling routine"""
    try:
        # Receive initial handshake
        data = client_socket.recv(256)
        if not data:
            return

        # Parse hostname from packet
        hostname = extract_hostname(data)
        print(f"New connection for: {hostname}")

        backend_info = redis_client.get(f"srv:{hostname}")
        # This is really good for us, because of the TTL - we can use it for automatic removal if invoices are not paid. (Just make ttl = ttl+30d for each payment)

        if backend_info is None:
            return send_motd(client_socket, "§eServer not found")
        else:
            try:
                backend_data = json.loads(backend_info)
                target_ip, target_port = backend_data["backend"].split(":") if ":" in backend_data["backend"] else (backend_data["backend"], None)
                # // if port is not specified, default to 25565
                target_port = int(target_port) if target_port else 25565
            except (json.JSONDecodeError, KeyError, ValueError):
                return send_motd(client_socket, "§cBackend Configuration error. Please refer to documentation.")
                # print(f"Invalid backend info for hostname: {hostname}")
                # client_socket.close()
                # return send_motd(client_socket, "§cBackend Configuration error. Please refer to documentation for help.")

        print(f"Connecting to backend: {target_ip}:{target_port}")
        # target_port = 25565

        # Connect to backend
        backend_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_socket.connect((target_ip, target_port))
        backend_socket.send(data)  # Forward initial packet

        # Start bidirectional forwarding
        ForwardThread(client_socket, backend_socket).start()  # Client -> Server
        ForwardThread(backend_socket, client_socket).start()  # Server -> Client

    except Exception as e:
        print(f"Connection error: {e}")
        client_socket.close()


def start_proxy(bind_port):
    """Start proxy server"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('0.0.0.0', bind_port))
    server.listen()
    print(f"Proxy listening on :{bind_port}")

    while True:
        client_socket, addr = server.accept()
        print(f"New client: {addr[0]}:{addr[1]}")
        threading.Thread(target=handle_client, args=(client_socket,)).start()


if __name__ == "__main__":
    #  get args from command line
    parser = argparse.ArgumentParser(description='MC-Secure Proxy Server')
    parser.add_argument('--port', type=int, default=25565, help='Port to listen on')
    args = parser.parse_args()
    # Start the proxy server
    start_proxy(args.port)