"""A module for constructing and deconstructing a packet."""

import struct


# Custom ERROR
class LengthError(Exception):
    def __init__(self, message):
        self.message = message


# CONSTANTS
BLOCK_SIZE = 1024

NAME_LENGTH = 150

# ENCODINGS

# Req_number, create/open, File name
OPEN_ENCODING = '!ii' + str(NAME_LENGTH) + 's'

# Req_number, Fd, starting pos, current_number_of_packet, total_packets, size_of_data, data
WRITE_ENCODING = '!iiiiii' + str(BLOCK_SIZE) + 's'

# Req_number, Fd, length
READ_REQ_ENCODING = '!iii'

# ACK for request with Req_number
ACK_ENCODING = '!i'


# Encode the packet for the create request
def construct_open_packet(req_number, create, name):
    if (len(name) > NAME_LENGTH):
        raise LengthError, "Too big name"
    return struct.pack(OPEN_ENCODING, req_number, name)


# Encode the packet for the write request
def construct_write_packet(req_number, fd, pos, cur_num, total, data):
    if (len(data) > BLOCK_SIZE):
        raise LengthError, "Too many data"
    return struct.pack(WRITE_ENCODING, req_number, fd, pos, cur_num, total, len(data), data)


# Encode the packet for the read request
def construct_read_packet(req_number, fd, length):
    if (size <= 0):
        raise LengthError, "Unacceptable Length"
    return struct.pack(READ_REQ_ENCODING, req_number, fd, length)


# Encode the packet for the read request
def construct_ACK(req_number):
    return struct.pack(ACK_ENCODING, req_number)


# Deconstruct a packet
def deconstruct_packet(decode, packet):
    return struct.unpack(decode, packet)
