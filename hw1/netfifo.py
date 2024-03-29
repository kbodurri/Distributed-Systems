"""
    API for FIFO pipe with UDP/IP connection
"""

import sys
#the socket dir contains MySocket_library.py
sys.path.append('../sockets/')
from MySocket_library import *
import socket
import struct
import thread
import time
import os


#Custom Errors
class TimeError(Exception):
   def __init__(self, value):
       self.value = value
   def __str__(self):
       return repr(self.value)

class LengthError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

class ReceiverError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


###############################################################################
################################## CONSTANTS ##################################

#Packets details
ACK_PACKET_SIZE = 16
DATA_PAYLOAD_SIZE = 2036
DATA_PACKET_SIZE = DATA_PAYLOAD_SIZE + 12

"""
    data packet encode: ! -> network
                        q -> long long integer (number of packet)
                        s -> string (payload=Data)
                        i -> integer

    ACK packet encode: ! -> network
                       q -> long long integer (number of packet)
                       q -> long long integer (empty spaces)
"""

DATA_ENCODE = '!q' + str(DATA_PAYLOAD_SIZE) + 's' + 'i'
ACK_ENCODE = '!qq'

NPACKET_INDEX = 0
PAYLOAD_INDEX = 1


#waiting time (float) to receive a packet! (in seconds)
TIMEOUT = 0.4

#Max number of packets receiver can ask for
MAX_NUM_OF_PACKETS = 15

###############################################################################
################################## VARIABLES ##################################

#closing variables!!!
end_of_trans = 0

close_mtx = thread.allocate_lock()
close_mtx.acquire()

error = 0

fd_list = []



################################# <RCV> #################################
#mutex for app. If the packet it requests isnt in the buffer app_wait = 1 and app_wait_mtx.acquire()
rcv_app_wait_mtx = thread.allocate_lock()
rcv_app_wait_mtx.acquire()
rcv_app_wait = 0

#mutex for sychronization between receiving thread and app
rcv_thread_app_mtx = thread.allocate_lock()

#next packet app wants to read from buffer (starts from 1)
rcv_next_app_read = 1
#next packet thread wants to read from socket (starts from 1)
rcv_next_waiting = 1
#number of packets in buffer
rcv_in_buffer = 0
#buffer (dictionary [key: payload])
rcv_buf = {}

#Receiver's Buffer size
rcv_buffer_size = 0

#counter for total received packets
rcv_total_packets = 0

#counter for dropped packets
rcv_dropped_packets = 0
################################# </RCV> #################################


################################# <SEND> #################################
#mutex for app. If buffer full, app_wait = 1 and app_wait_mtx.acquire()
snd_app_wait_mtx = thread.allocate_lock()
snd_app_wait_mtx.acquire()
snd_app_wait = 0

snd_thread_wait_mtx = thread.allocate_lock()
snd_thread_wait_mtx.acquire()
snd_thread_wait = 0

#mutex for sychronization between sending thread and app
snd_thread_app_mtx = thread.allocate_lock()

#next packet app wants to read from buffer (starts from 1)
snd_next_app_write = 1
#next packet thread wants to read from socket (starts from 1)
snd_next_sending = 1
#number of packets in buffer
snd_in_buffer = 0
#buffer (dictionary [key: payload])
snd_buf = {}

#Sender's buffer size
snd_buffer_size = 0

#counter for total send packets
snd_total_packets = 0
################################# </SEND> #################################



###############################################################################
################################## FUNCTIONS ##################################




############################ <PACKET FUNCTIONS> ############################
""" packet format: data_packet --> [number_of_packet,data,len_of_valid_data]
                   ack_packet  --> [number_of_packet,next_number_sequence]
                   rtt_packet  --> [RTT]
"""
def construct_packet(encode, number_of_packet, payload, size_of_valid_data):
    if (encode == '!qq'):
        return struct.pack(encode,number_of_packet,payload)
    else:
        return struct.pack(encode,number_of_packet,payload,size_of_valid_data)

def deconstruct_packet(decode, packet):
    try:
        if (decode == '!qq'):
            return struct.unpack(decode,packet)
        else:
            npacket , data , len_valid_data = struct.unpack(decode,packet)
            return (npacket,data[:len_valid_data])

    except struct.error:
        raise LengthError
############################ </PACKET FUNCTIONS> ############################




##################### <PRIVATE FUNCTION FOR THREADS> #####################
#receive packet with timeout!
def packet_receive(sock, size_of_packet, decode):

        #wait for next packet
        try:
            return_packet = sock.ReceiveFrom(size_of_packet)

            #In case the packet is broken deconstuct_packet returns LengthError
            try:
                data = deconstruct_packet(decode,return_packet[0])
            except LengthError:
                raise LengthError ("Received broken packet")

            addr = return_packet[1]
            return (data,addr)

        except socket.timeout:
                raise TimeError ("Packet lost")
##################### </PRIVATE FUNCTION FOR THREADS> #####################



############################ <THREADS CODE> ############################
def rcv_thread (sock):


    global rcv_next_waiting
    global rcv_next_app_read
    global rcv_in_buffer
    global rcv_buffer_size
    global rcv_dropped_packets
    global rcv_total_packets

    #The first time wait dor only one packet
    num_of_packets = 1


    #The only way to exti while is by closing receiver (end_of_trans)
    while (True):

        #Number o f packets we didn't receive
        missed = 0


        #Try to receive num_of_packets packets
        for p in xrange (num_of_packets):

	        #check for close
            rcv_thread_app_mtx.acquire()
            if (end_of_trans == 1):
                rcv_thread_app_mtx.release()
                break
            rcv_thread_app_mtx.release()

            #Receive next packet
            try:
                data, addr = packet_receive(sock, DATA_PACKET_SIZE, DATA_ENCODE)
                rcv_total_packets += 1
            except:
                missed += 1
                continue

            #print "RCV_THREAD: got", data

            rcv_thread_app_mtx.acquire()


            #Check if packet key is within window [rcv_next_waiting - rcv_next_waiting + num_of_packets] and buffer has empty space in order to save its
            if (data[NPACKET_INDEX] >= rcv_next_waiting and data[NPACKET_INDEX] <= rcv_next_waiting + num_of_packets and data[NPACKET_INDEX]-rcv_next_waiting < rcv_buffer_size-rcv_in_buffer):

                #Not allowed duplicate packets
                if (not rcv_buf.has_key(data[NPACKET_INDEX])):
                    rcv_buf.update( {data[NPACKET_INDEX]: data[PAYLOAD_INDEX]} )


                    if (data[NPACKET_INDEX] == rcv_next_waiting + 1 and rcv_buf.has_key(rcv_next_waiting)):
                        rcv_next_waiting += 1

                    #Update counter
                    rcv_in_buffer += 1

                    #print "Rcv_thread: Received packet" ,data[NPACKET_INDEX] , "(in:", rcv_in_buffer, ")"
                    #print "Rec Buffer:", rcv_buf, "(", len(rcv_buf), ")"


                else: #already in buffer
                    rcv_dropped_packets += 1


            else: #buffer full
                rcv_dropped_packets += 1


            rcv_thread_app_mtx.release()


        rcv_thread_app_mtx.acquire()

        #check for close
        if (end_of_trans==1):
            break

        #Not a single packet received
        if (missed == num_of_packets):

            #if app waits give it priority
            if (rcv_app_wait and rcv_next_app_read < rcv_next_waiting):
                rcv_app_wait_mtx.release()
            else:
                rcv_thread_app_mtx.release()
            continue


        #Find the first missing packet
        while (rcv_buf.has_key(rcv_next_waiting)):
            rcv_next_waiting += 1


        #In case buffer is full we send 1 empty space instead of 0 --- else we send at most MAX_NUM_OF_PACKETS empty spaces
        num_of_packets = min(MAX_NUM_OF_PACKETS, max(1, rcv_buffer_size - rcv_in_buffer))
        #send ACK for next num_of_packets packets
        ack_packet = construct_packet(ACK_ENCODE, rcv_next_waiting, num_of_packets, None)
        sock.SendTo(ack_packet, addr)
        #print "RCV_THREAD: Send ACK with seq number: ", rcv_next_waiting, "and num_of_packets: ", num_of_packets


        #if app waits give it priority
        if (rcv_app_wait and rcv_next_app_read < rcv_next_waiting):
            rcv_app_wait_mtx.release()
        else:
            rcv_thread_app_mtx.release()



    rcv_thread_app_mtx.release()
    print 'End of Receiving!'
    close_mtx.release()

def snd_thread (sock):

    global snd_next_sending
    global snd_in_buffer
    global snd_app_wait
    global snd_thread_wait
    global end_of_trans
    global error
    global snd_total_packets


    #The first time send only one packet
    num_of_packets = 1

    #The only way to exit while is by closing sender or if an error occur
    while (True):


        snd_thread_app_mtx.acquire()

        #Waits till buffer has at least num_of_packets packets (if num_of_packets > buffer_size we reduce num_of_packets to buffer_size)
        #Except if close has been called (end_of_trans == 1) which means we must send the remaining packets
        while (snd_in_buffer < min(num_of_packets, snd_buffer_size) and (not end_of_trans) ):
            #print "SND_THREAD: Wait for new packet"
            snd_thread_wait = 1
            snd_thread_app_mtx.release()
            snd_thread_wait_mtx.acquire()
            snd_thread_app_mtx.acquire()

        snd_thread_app_mtx.release()


        plus = 0

        #Send num_of_packets packets
        for i in xrange (num_of_packets):

            snd_thread_app_mtx.acquire()

            if (error):
                snd_thread_app_mtx.release()
                break

            #Next packet is missing (when we send the last packets)
            if (snd_buf.has_key(snd_next_sending+plus)):
                #Send next packet
                try:
                    #print "SND_THREAD: Sending packet", snd_next_sending+i
                    sock.Send(snd_buf[snd_next_sending+plus])
                    snd_total_packets += 1
                    plus += 1

                except socket.error:
                    error = 1
                    end_of_trans = 1

                    #Wake app if is blocked and exit
                    if (snd_app_wait == 1):
                        snd_app_wait=0
                        snd_app_wait_mtx.release()

                    snd_thread_app_mtx.release()

                    break

            snd_thread_app_mtx.release()


        snd_thread_app_mtx.acquire()

        #exit if error occured
        if (error):
            break

        #Wait for ACK
        try:
            ack = packet_receive(sock, ACK_PACKET_SIZE, ACK_ENCODE)[0]
            #print "SND_THREAD: Took ack with seq_num: ", ack[0], "and empty_spaces: ", ack[1]

            #Update buffer
            for i in xrange (snd_next_sending, ack[0]):
                del snd_buf[i]
                snd_in_buffer -= 1

            #Update variables
            snd_next_sending = ack[0]
            num_of_packets = ack[1]

            #All packets have been send so we can exit
            if (end_of_trans and snd_in_buffer == 0):
                break

        except TimeError, LengthError:
            pass
            #ACK not received
            #send only one packet
            #print "SND_THREAD: ACK not received"
        except socket.error:
            #socket.error == unreachable Receiver
            #So we exit
            error = 1
            end_of_trans = 1

            #Wake app if is blocked and exit
            if (snd_app_wait == 1):
                snd_app_wait=0
                snd_app_wait_mtx.release()

            break

        #Wake app if is blocked
        if (snd_app_wait == 1):
            snd_app_wait=0
            snd_app_wait_mtx.release()

        snd_thread_app_mtx.release()


    snd_thread_app_mtx.release()
    print "SND_THREAD: End of transmision"
    close_mtx.release()
############################ </THREADS CODE> ############################




############################ <BASIC LIBRARY FUNCTIONS> ############################

#Open reading side of pipe. Return a positive integer as file discriptor
def netfifo_rcv_open(port,bufsize):

    #initial buffer
    global rcv_buffer_size
    rcv_buffer_size = max(1,bufsize/DATA_PAYLOAD_SIZE)

    #create Server object (reading side)
    socket_object = SocketServer(socket.AF_INET, socket.SOCK_DGRAM, TIMEOUT, '', port, 0)

    #print Receiver's Informations
    print 'IP: ' , socket_object.GetIP(),
    print 'Port: ', socket_object.GetPortNumber()


    #start the thread
    thread.start_new_thread (rcv_thread, (socket_object, ))

    fd_list.append(socket_object)

    return fd_list.index(socket_object)

#reading from pipe. Return data.
def netfifo_read(fd,size):

    global rcv_next_app_read
    global rcv_app_wait
    global rcv_in_buffer
    global rcv_buffer_size

    #Returning value
    s = ""

    while (len(s) <= size):

        #app wants to read packet next_app_read
        rcv_thread_app_mtx.acquire()

        while (rcv_next_app_read not in rcv_buf):
            #if packet not in buf wait
            #print "READ_APP: waits for packet " ,rcv_next_app_read
            rcv_app_wait = 1
            rcv_thread_app_mtx.release()
            rcv_app_wait_mtx.acquire()
            rcv_app_wait = 0


        #Get next packet from buffer and delete it
        d = rcv_buf[rcv_next_app_read]
        del rcv_buf[rcv_next_app_read]
        rcv_next_app_read += 1
        rcv_in_buffer -= 1


        #Occasional garbage collector
        if (rcv_next_app_read%1000 == 0):
            for k in (rcv_buf.keys()):
                if (k < rcv_next_app_read):
                    del rcv_buf[k]
                    rcv_in_buffer -= 1


        #print "READ_APP: got packet", rcv_next_app_read-1

        rcv_thread_app_mtx.release()

        #Reveived empty packet which means end of transmision so exit
        if (d == "" and len(s)>0):
            break

        #update value of s
        s = s + d
        #print "READ_APP: has till now: ", s

    return s

#close reading side
def netfifo_rcv_close(fd):

    global end_of_trans
    global rcv_dropped_packets
    global rcv_total_packets

    sock = fd_list[fd]

    #Update end_of_trans in order to inform thread and app
    rcv_thread_app_mtx.acquire()
    end_of_trans=1
    rcv_thread_app_mtx.release()

    close_mtx.acquire()


    #Print measurements
    print 'Total Received packets !!!! ---->', rcv_total_packets
    print 'Dropped packets !!!! ---->', rcv_dropped_packets

    sock.Close()





#Open writing side of pipe. Return a positive integer as file discriptor
def netfifo_snd_open(host,port,bufsize):

    #initial buffer
    global snd_buffer_size
    snd_buffer_size = max(1,bufsize/DATA_PAYLOAD_SIZE)

    global error
    global end_of_trans
    global snd_in_buffer
    global snd_buf
    global snd_total_packets


    #Initialize Sender's variables
    error = 0
    end_of_trans = 0
    snd_in_buffer = 0
    snd_buf = {}

    #create Client object (writing side)
    socket_object = SocketClient(socket.AF_INET, socket.SOCK_DGRAM, TIMEOUT, 0)
    socket_object.Connect(host,port)

    #start the snd_thread
    thread.start_new_thread (snd_thread, (socket_object, ))

    fd_list.append(socket_object)

    return fd_list.index(socket_object)


#writing data in pipe
def netfifo_write(fd,buf,size):

    global snd_next_app_write
    global snd_in_buffer
    global snd_thread_wait
    global snd_buffer_size
    global snd_app_wait
    global error


    for s in xrange (0, size, DATA_PAYLOAD_SIZE):

        #Create next packet
        packet = construct_packet(DATA_ENCODE,snd_next_app_write, buf[s: s+ DATA_PAYLOAD_SIZE],len(buf[s: s+ DATA_PAYLOAD_SIZE]))

        snd_thread_app_mtx.acquire()

        #print "WRITE_APP: tries to add packet", snd_next_app_write

        #Wait till buffer has empty space
        while (snd_in_buffer == snd_buffer_size and (not error)):
            #print "WRITE_APP: waits for empty position"
            snd_app_wait = 1
            snd_thread_app_mtx.release()
            snd_app_wait_mtx.acquire()
            snd_thread_app_mtx.acquire()

        #Check if error occured and exit
        if (error):
            snd_thread_app_mtx.release()
            raise ReceiverError ("Unreachable Receiver")
            break

        #Insert new packet in buffer
        snd_buf.update ({snd_next_app_write: packet})


        #Update variables
        snd_next_app_write += 1
        snd_in_buffer += 1

        #print "WRITE_APP: added packet", snd_next_app_write-1,"(in:", snd_in_buffer, ")"

        #Wake thread if is blocked
        if (snd_thread_wait == 1):
            snd_thread_wait=0
            snd_thread_wait_mtx.release()


        snd_thread_app_mtx.release()


#close writing side
def netfifo_snd_close(fd):

    global error
    global end_of_trans
    global snd_thread_wait
    global snd_total_packets
    global snd_next_sending

    #Check if thread is already closed because of an error
    snd_thread_app_mtx.acquire()
    if (error):
        del fd_list[fd]
        snd_thread_app_mtx.release()
        return
    snd_thread_app_mtx.release()


    sock = fd_list[fd]


    netfifo_write (fd, "", 1)

    snd_thread_app_mtx.acquire()

    #Update end_of_trans
    end_of_trans = 1

    #Wake thread if is blocked
    if (snd_thread_wait == 1):
        snd_thread_wait_mtx.release()

    snd_thread_app_mtx.release()

    close_mtx.acquire()

    #Print measurements
    print 'Retrans packets !!!!!!---->', snd_total_packets - snd_next_sending +1

    sock.Close()

    del fd_list[fd]
############################ </BASIC LIBRARY FUNCTIONS> ############################
