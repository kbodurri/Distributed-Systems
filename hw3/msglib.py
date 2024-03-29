"""
    API for join/leave a group.

    Also send/receive msg from group chat.
    When join is called we create three threads. One listening to DIrSvc,
    one listening to multicast and one sending to multicast.
"""

import socket
import thread
import select
import time
import datetime

from packet_struct import *
from multicast_module import *

now = datetime.datetime.now()

# ............................. <Variables> ............................. #

# Time to retransmit the message if not received ACK
RESEND_TIMEOUT = 1

# Every ACK_TIMEOUT send ACK for the last_acked_seq_number
ACK_TIMEOUT = 2


first_time = True

# A lock for all the buffers
buffers_lock = thread.allocate_lock()

# ........................ <Coordinator buffers> ........................ #

# True or False if is the coordinator for each group
grp_info_coordinator = {}
# Only for the coordinator (the sender of the valid message for each seq_num)
grp_info_valid_messages = {}
# Biggest sequence number which has been ACKed
last_acked_seq_number = {}
# The time.time() of the last send of the last_acked_seq_number ACK
last_time_ACK_send = {}

# ........................ </Coordinator buffers> ........................ #

# The name the client has in each group chat
grp_info_my_name = {}
# The group info (ip, port) for every grp_socket
grp_sockets_grp_info = {}
# The group socket for every group
grp_info_grp_sockets = {}
# The group info (ip, port) for every service_socket
service_conn_grp_info = {}
# The members of each group
grp_info_members = {}

# Last seq_num for which we got ACK
last_valid_number = {}
# Last seq_number the app got
last_read_number = {}
# Sequence numbers for which we dont have the message although it was acked
missing_seq_nums = {}

# All service connections and all multicast connections
total_service_conn = []
total_grp_sockets = []

# Services variables
service_addr = ()

# The messages received from multicast
recv_messages = {}
recv_messages_lock = thread.allocate_lock()

# The messages which are going to be send
send_messages = {}
# [Message, Seq_num with which was send, time.time() when send]
send_messages_lock = thread.allocate_lock()

acked_messages = {}
acked_messages_lock = thread.allocate_lock()

# The messages received from DirSvc
service_messages = {}
service_messages_lock = thread.allocate_lock()

# ............................. </Variables> ............................. #

# ............................. <API> ............................. #


# Set the service address
def grp_setDir(diripaddr, dirport):

    global service_addr
    service_addr = (diripaddr, dirport)


# Join a group chat.
def grp_join(grp_ipaddr, grp_port, myid):

    global service_addr, service_conn, my_name, grp_socket, first_time

    grp_pair = (grp_ipaddr, grp_port)

    s = socket.socket()
    s.connect(service_addr)

    request_for_grp = construct_join_packet(grp_ipaddr, grp_port, myid)
    # Send a request to connect
    s.send(request_for_grp)

    buffers_lock.acquire()

    grp_info_coordinator[grp_pair] = False
    grp_info_valid_messages[grp_pair] = {}
    last_time_ACK_send[grp_pair] = 0

    # Create a list with the already connected users
    grp_info_members[grp_pair] = []

    while (True):

        packet = s.recv(150)

        name = deconstruct_packet(PREVIOUS_MEMBERS_ENCODING, packet)[0]
        name = name.strip('\0')

        if (name == myid):
            break

        grp_info_members[grp_pair].append(name)

    # If no one before -> he is the coordinator
    if (grp_info_members[grp_pair] == []):
        grp_info_coordinator[grp_pair] = True

    buffers_lock.release()

    # Try till you get a valid multicast socket
    grp_socket = -1
    while (grp_socket == -1):
        grp_socket = socket_for_multicast(grp_ipaddr, grp_port)

    buffers_lock.acquire()

    # Update buffers
    grp_info_my_name[grp_pair] = myid

    service_conn_grp_info[s] = grp_pair

    grp_sockets_grp_info[grp_socket] = grp_pair
    grp_info_grp_sockets[grp_pair] = grp_socket

    last_acked_seq_number[grp_pair] = 0
    last_read_number[grp_pair] = 1
    last_valid_number[grp_pair] = 0

    total_service_conn.append(s)
    total_grp_sockets.append(grp_socket)

    missing_seq_nums[grp_pair] = []

    buffers_lock.release()

    recv_messages_lock.acquire()
    recv_messages[grp_pair] = {}
    recv_messages_lock.release()

    send_messages_lock.acquire()
    send_messages[grp_pair] = []
    send_messages_lock.release()

    service_messages_lock.acquire()
    service_messages[grp_pair] = []
    service_messages_lock.release()

    acked_messages_lock.acquire()
    acked_messages[grp_pair] = {}
    acked_messages_lock.release()

    # Start the threads only the first time
    if (first_time):
        thread.start_new_thread(listen_from_DirSvc, ())
        thread.start_new_thread(listen_from_multicast, ())
        thread.start_new_thread(send_to_multicast, ())
        first_time = False

    return grp_socket


# Leave a group.
def grp_leave(gsocket):

    # Get group chat info and the name he has

    buffers_lock.acquire()

    grp_ipaddr = grp_sockets_grp_info[gsocket][0]
    grp_port = grp_sockets_grp_info[gsocket][1]
    my_name = grp_info_my_name[(grp_ipaddr, grp_port)]

    grp_pair = (grp_ipaddr, grp_port)

    if (grp_info_coordinator[grp_pair]):
        last_seq_num = last_acked_seq_number[grp_pair]
    else:
        last_seq_num = -1

    service_conn = None

    for s in service_conn_grp_info.keys():
        if (service_conn_grp_info[s] == grp_pair):
            service_conn = s
            break

    buffers_lock.release()

    request_for_dis = construct_leave_packet(grp_ipaddr, grp_port, my_name, last_seq_num)

    service_conn.send(request_for_dis)

    gsocket.close()


# Return the next message
def grp_recv(gsocket):


    # Get group info
    buffers_lock.acquire()
    grp_ipaddr = grp_sockets_grp_info[gsocket][0]
    grp_port = grp_sockets_grp_info[gsocket][1]
    grp_pair = (grp_ipaddr, grp_port)
    buffers_lock.release()

    # The message which is returned
    m = None
    t = None

    while (m == None or t == None):

        # First check service_messages
        service_messages_lock.acquire()

        if (len(service_messages[grp_pair]) > 0):

            m = service_messages[grp_pair][0][0]
            t = service_messages[grp_pair][0][1]

            if (t == -2):
                # Type -2 means disconnected from grp_pair
                # So delete the receive messages buffers
                del service_messages[grp_pair]

                service_messages_lock.release()

                buffers_lock.acquire()

                del grp_info_coordinator[grp_pair]
                del grp_info_valid_messages[grp_pair]
                del last_acked_seq_number[grp_pair]

                del grp_info_grp_sockets[grp_pair]
                del grp_sockets_grp_info[gsocket]

                del grp_info_members[grp_pair]
                del grp_info_my_name[grp_pair]

                del last_valid_number[grp_pair]
                del last_read_number[grp_pair]
                del missing_seq_nums[grp_pair]

                total_grp_sockets.remove(gsocket)

                buffers_lock.release()

                send_messages_lock.acquire()
                del send_messages[grp_pair]
                send_messages_lock.release()

                acked_messages_lock.acquire()
                del acked_messages[grp_pair]
                acked_messages_lock.release()

                recv_messages_lock.acquire()
                del recv_messages[grp_pair]
                recv_messages_lock.release()

            else:
                del service_messages[grp_pair][0]

                service_messages_lock.release()

            break

        else:

            service_messages_lock.release()
            # Then check group messages
            recv_messages_lock.acquire()

            # First check if we received a message with seq_number == last_read_number
            if (last_read_number[grp_pair] in recv_messages[grp_pair]):
                # Then check if this message is valid (we have only one message for this seq number with True)
                if (len(recv_messages[grp_pair][last_read_number[grp_pair]]) == 1 and recv_messages[grp_pair][last_read_number[grp_pair]][0][2] == True):

                    m = recv_messages[grp_pair][last_read_number[grp_pair]][0][0] + ": " + recv_messages[grp_pair][last_read_number[grp_pair]][0][1]
                    # All messages are type 0
                    t = 0
                    # The client got the message, so delete it from internal buffer
                    del recv_messages[grp_pair][last_read_number[grp_pair]]

                    last_read_number[grp_pair] += 1

                    recv_messages_lock.release()

                    break

            recv_messages_lock.release()

        time.sleep(0.0009)

    return m, t


# Add to send_messages the new message
def grp_send(gsocket, message):

    # Get group info
    buffers_lock.acquire()
    grp_ipaddr = grp_sockets_grp_info[gsocket][0]
    grp_port = grp_sockets_grp_info[gsocket][1]
    grp_pair = (grp_ipaddr, grp_port)
    buffers_lock.release()

    send_messages_lock.acquire()
    send_messages[grp_pair].append([message, -1, -1])
    send_messages_lock.release()

# ............................. </API> ............................. #


# ............................. <THREADS> ............................. #


# The thread which receives messages from DirSvc
def listen_from_DirSvc():

    global total_service_conn

    while (True):

        buffers_lock.acquire()
        current_service_conn = total_service_conn
        buffers_lock.release()

        # Listen from all the service connections
        ready, _, _ = select.select(current_service_conn, [], [], 1)

        for service_conn in ready:

            packet = service_conn.recv(1024)
            name, state, last_acked_seq_num = deconstruct_packet(MEMBER_CONN_DIS_ENCODING, packet)

            name = name.strip('\0')

            # Find group info from service_conn_grp_info
            buffers_lock.acquire()
            grp_ipaddr = service_conn_grp_info[service_conn][0]
            grp_port = service_conn_grp_info[service_conn][1]
            grp_pair = (grp_ipaddr, grp_port)
            buffers_lock.release()

            # Add the new message to the queue with the correct message type
            service_messages_lock.acquire()

            # Case 1: Someone is connected
            if (state == 1):
                service_messages[grp_pair].append([name + " is connected", 1])

                # Update members list
                buffers_lock.acquire()
                grp_info_members[grp_pair].append(name)

                # If i am the coordinator send ACK for the last seq_num so the new member will take the old messages
                if (grp_info_coordinator[grp_pair]):
                    grp_socket = grp_info_grp_sockets[grp_pair]

                    biggest_seq_num = last_acked_seq_number[grp_pair]

                    # The first time only (biggest_seq_num == 0)
                    if (biggest_seq_num in grp_info_valid_messages[grp_pair]):

                        name = grp_info_valid_messages[grp_pair][biggest_seq_num]

                        time.sleep (0.2)

                        packet = construct_valid_message_packet(name, biggest_seq_num)
                        grp_socket.sendto(packet, grp_pair)

                buffers_lock.release()

            # Case 2: Someone is disconnected
            elif (state == -1):

                buffers_lock.acquire()

                # Update members list
                grp_info_members[grp_pair].remove(name)

                # I disconnected (delete and close everything)
                if (name == grp_info_my_name[grp_pair]):

                    # Case 2.1: Message type -2 because i disconnected
                    service_messages[grp_pair].append(["Disconnected from group chat successfully", -2])

                    service_conn.close()

                    total_service_conn.remove(service_conn)

                    del service_conn_grp_info[service_conn]

                    buffers_lock.release()

                    service_messages_lock.release()

                    continue

                # Elegxw an eimai twra o prwtos pleon sto group opote prepei na ginw coordinator
                if (grp_info_members[grp_pair] != [] and not grp_info_coordinator[grp_pair]):

                    # If i am the first i must become the coordinator
                    if (grp_info_members[grp_pair][0] == grp_info_my_name[grp_pair]):
                        grp_info_coordinator[grp_pair] = True
                        last_acked_seq_number[grp_pair]  = last_acked_seq_num

                elif (grp_info_members[grp_pair] == []):
                    del grp_info_members[grp_pair]

                buffers_lock.release()

                # Case 2.2: Message type -1 because someone else disconnected
                service_messages[grp_pair].append([name + " is disconnected", -1])

            service_messages_lock.release()


# The thread which receives messages from multicasts
def listen_from_multicast():

    global total_grp_sockets, RESEND_TIMEOUT

    while (True):

        buffers_lock.acquire()
        current_grp_sockets = total_grp_sockets
        buffers_lock.release()

        try:
            ready, _, _ = select.select(current_grp_sockets, [], [], 1)
        except socket.error:
            pass

        for grp_socket in ready:

            packet, addr = grp_socket.recvfrom(1024)

            if (len(packet) == 4):

                # Received request for a previous message

                requested_seq_num = deconstruct_packet(PREVIOUS_MESSAGE_REQUEST_ENCODING, packet)[0]

                # print "Received request for", requested_seq_num

                # Get group info, name and socket
                buffers_lock.acquire()

                grp_ipaddr = grp_sockets_grp_info[grp_socket][0]
                grp_port = grp_sockets_grp_info[grp_socket][1]
                grp_pair = (grp_ipaddr, grp_port)

                name = grp_info_my_name[grp_pair]

                grp_socket = grp_info_grp_sockets[grp_pair]

                buffers_lock.release()

                acked_messages_lock.acquire()

                # I am the sender of the message with this seq_num so I send it
                if (requested_seq_num in acked_messages[grp_pair].keys()):
                    packet = construct_message_packet(name, acked_messages[grp_pair][requested_seq_num], requested_seq_num)
                    grp_socket.sendto(packet, grp_pair)
                    # print "Send", acked_messages[grp_pair][requested_seq_num], "with", requested_seq_num, "because of request"

                acked_messages_lock.release()


            elif (len(packet) == 154):

                # Received ACK for a message from the coordinator

                name, seq_num = deconstruct_packet(VALID_MESSAGE, packet)
                name = name.strip('\0')

                # print"Received ACK for", seq_num, name, time.time()

                buffers_lock.acquire()

                # Get group info
                grp_ipaddr = grp_sockets_grp_info[grp_socket][0]
                grp_port = grp_sockets_grp_info[grp_socket][1]
                grp_pair = (grp_ipaddr, grp_port)

                # Already received this ACK
                if (last_read_number[grp_pair] > seq_num):
                    buffers_lock.release()
                    continue

                # Check if missed any previous message
                if (last_valid_number[grp_pair]+1 != seq_num):
                    for i in xrange(last_valid_number[grp_pair]+1, seq_num):

                        already_in = False
                        for pos in xrange(len(missing_seq_nums[grp_pair])):
                            if (missing_seq_nums[grp_pair][pos][0] == i):
                                already_in = True
                                break

                        if (not already_in):
                            missing_seq_nums[grp_pair].append([i, -1])

                        # print "Missing seq_nums", missing_seq_nums

                last_valid_number[grp_pair] = max(last_valid_number[grp_pair], seq_num)

                # print "Last valid number", last_valid_number[grp_pair]

                # Received ACK for my packet, must save message to acked_messages
                if (grp_info_my_name[grp_pair] == name):

                    buffers_lock.release()

                    send_messages_lock.acquire()

                    for i in xrange(len(send_messages[grp_pair])):
                        if (send_messages[grp_pair][i][1] == seq_num):

                            # Save it at acked_messages and delete it from send_messages
                            acked_messages_lock.acquire()
                            acked_messages[grp_pair][seq_num] = send_messages[grp_pair][i][0]
                            acked_messages_lock.release()

                            RESEND_TIMEOUT = 2*(time.time() - send_messages[grp_pair][i][2])

                            # print "Set RESEND_TIMEOUT to", RESEND_TIMEOUT

                            del send_messages[grp_pair][i]

                            break

                    send_messages_lock.release()

                else:
                    buffers_lock.release()

                recv_messages_lock.acquire()

                found_message = False

                # Search for the message at recv_messages
                if (seq_num in recv_messages[grp_pair]):

                    i = 0
                    while (i < len(recv_messages[grp_pair][seq_num])):

                        # Delete every other message with this seq_num
                        if (recv_messages[grp_pair][seq_num][i][0] != name):
                            del recv_messages[grp_pair][seq_num][i]
                        else:
                            # Make it valid (True)
                            found_message = True
                            recv_messages[grp_pair][seq_num][i][2] = True
                            # print "Message found", recv_messages
                            i += 1

                recv_messages_lock.release()

                if (not found_message):
                    buffers_lock.acquire()

                    already_in = False
                    for pos in xrange(len(missing_seq_nums[grp_pair])):
                        if (missing_seq_nums[grp_pair][pos][0] == seq_num):
                            already_in = True
                            break

                    if (not already_in):
                        missing_seq_nums[grp_pair].append([seq_num, -1])

                    # print "Message not found", missing_seq_nums
                    buffers_lock.release()

            elif (len(packet) == 1024):
                # Received a new message
                name, message, seq_num = deconstruct_packet(MESSAGE_ENCODING, packet)

                name = name.strip('\0')
                message = message.strip('\0')

                # print"Received", message, "from", name, "with", seq_num, time.time()

                buffers_lock.acquire()

                # Get group info
                grp_ipaddr = grp_sockets_grp_info[grp_socket][0]
                grp_port = grp_sockets_grp_info[grp_socket][1]
                grp_pair = (grp_ipaddr, grp_port)

                # 1.    Check if is the coordinator
                # 2.    If the coordinator then check
                #       if the seq_num is the last_acked_seq_num + 1 then
                # 3.    send ACK for this packet and
                # 4.    Update last_acked_seq_number
                if (grp_info_coordinator[grp_pair]):

                    if (last_acked_seq_number[grp_pair] + 1 == seq_num):

                        grp_info_valid_messages[grp_pair][seq_num] = name

                        valid_message_packet = construct_valid_message_packet(name, seq_num)
                        grp_socket.sendto(valid_message_packet, grp_pair)

                        # print"Send ACK for first time for", seq_num, time.time()

                        # Update sequence number
                        last_acked_seq_number[grp_pair] = seq_num

                    # if already send ACK for this seq_num send it again
                    #elif (seq_num <= last_acked_seq_number[grp_pair] and name != grp_info_valid_messages[grp_pair][seq_num]):
                    elif (seq_num <= last_acked_seq_number[grp_pair]):

                        name = grp_info_valid_messages[grp_pair][seq_num]

                        valid_message_packet = construct_valid_message_packet(name, seq_num)
                        grp_socket.sendto(valid_message_packet, grp_pair)

                        # print "Send ACK again for", seq_num

                    elif (seq_num == last_acked_seq_number[grp_pair]):

                        name = grp_info_valid_messages[grp_pair][seq_num]

                        valid_message_packet = construct_valid_message_packet(name, seq_num)
                        grp_socket.sendto(valid_message_packet, grp_pair)

                # Already received this message
                if (last_read_number[grp_pair] > seq_num):
                    buffers_lock.release()
                    continue

                buffers_lock.release()

                # Update recv_messages
                recv_messages_lock.acquire()

                buffers_lock.acquire()

                # Search for the seq_num at missing_seq_nums
                is_missing = False

                for i in xrange(len(missing_seq_nums[grp_pair])):
                    if (missing_seq_nums[grp_pair][i][0] == seq_num):
                        is_missing = True
                        # print "Is retransmit for missing num", missing_seq_nums
                        del missing_seq_nums[grp_pair][i]
                        break

                buffers_lock.release()

                # If seq_num in missing_seq_nums then the message is from retransmit, so is valid (True)
                if (is_missing):
                    recv_messages[grp_pair][seq_num] = [[name, message, True]]

                # Na elegxw an einai retransmit na mhn to krataw
                elif (seq_num in recv_messages[grp_pair] and [name, message, False] not in recv_messages[grp_pair][seq_num] and [name, message, True] not in recv_messages[grp_pair][seq_num]):
                    recv_messages[grp_pair][seq_num].append([name, message, False])
                elif (seq_num not in recv_messages[grp_pair]):
                    recv_messages[grp_pair][seq_num] = [[name, message, False]]

                # print "Received messages", recv_messages

                recv_messages_lock.release()


# The thread which sends the messages to the mmulticasts
def send_to_multicast():

    while (True):

        send_messages_lock.acquire()

        # For each group send the first message from send_messages
        for grp_pair in send_messages.keys():

            buffers_lock.acquire()
            grp_socket = grp_info_grp_sockets[grp_pair]
            name = grp_info_my_name[grp_pair]
            buffers_lock.release()

            # Send only the first message
            if (len(send_messages[grp_pair]) > 0):

                # Send it only if RESEND_TIMEOUT has passed
                if(time.time() - send_messages[grp_pair][0][2] > RESEND_TIMEOUT):

                    send_messages[grp_pair][0][1] = last_valid_number[grp_pair] + 1
                    send_messages[grp_pair][0][2] = time.time()

                    buffers_lock.acquire()

                    # print"Send", send_messages[grp_pair][0][0], "with", last_valid_number[grp_pair] + 1, time.time()

                    packet = construct_message_packet(name, send_messages[grp_pair][0][0], last_valid_number[grp_pair] + 1)
                    buffers_lock.release()

                    grp_socket.sendto(packet, grp_pair)

        send_messages_lock.release()

        buffers_lock.acquire()

        # For each group send requests for the missing messages
        for grp_pair in missing_seq_nums.keys():

            grp_socket = grp_info_grp_sockets[grp_pair]

            # Send request for all the missing messages
            for i in xrange(len(missing_seq_nums[grp_pair])):

                # Send it only if RESEND_TIMEOUT has passed
                if (time.time() - missing_seq_nums[grp_pair][i][1] > RESEND_TIMEOUT):

                    missing_seq_nums[grp_pair][i][1] = time.time()

                    packet = construct_previous_message_request_packet(missing_seq_nums[grp_pair][i][0])

                    # print "Send request for", missing_seq_nums[grp_pair][i][0]

                    grp_socket.sendto(packet, grp_pair)

        # Check if I am the coordinator and send the last_acked_seq_number
        for grp_pair in grp_info_coordinator.keys():

            if (grp_info_coordinator[grp_pair] and (time.time() - last_time_ACK_send[grp_pair]) > ACK_TIMEOUT):

                grp_socket = grp_info_grp_sockets[grp_pair]

                biggest_seq_num = last_acked_seq_number[grp_pair]

                # The first time only (biggest_seq_num == 0)
                if (biggest_seq_num in grp_info_valid_messages[grp_pair]):

                    name = grp_info_valid_messages[grp_pair][biggest_seq_num]

                    last_time_ACK_send[grp_pair] = time.time()

                    packet = construct_valid_message_packet(name, biggest_seq_num)
                    grp_socket.sendto(packet, grp_pair)

        buffers_lock.release()

        time.sleep(0.0009)

# ............................. </THREADS> ............................. #
