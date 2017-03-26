import socket
import thread
import time
import select
from packet_struct import *
from multicast_module import *

# includes all tcp connections with clients
connection_buffer = []
connection_buffer_lock = thread.allocate_lock()


def establish_connection(client_addr):

    #Create the TCP socket
    tcp_socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    tcp_socket.settimeout(TIMEOUT)

    try:
        print 'Try connecting to IP: %s, port: %d' %(client_addr[0],client_addr[1])
        tcp_socket.connect(client_addr)


        #Check if connection establish!
        try:
            msg = tcp_socket.recv(5)
            print 'Server: %s connected to : %s' % (tcp_socket.getsockname(),tcp_socket.getpeername())
            print 'Connection complete'
            return tcp_socket
        except socket.error:
            print "Connection failed. Try again!"
            tcp_socket.close()
            return None

    except socket.timeout:
        print 'Timeout!! Try again to connect!'
        return establish_connection(client_addr)
    except socket.error:
        print "Connection failed. Try again!"
        tcp_socket.close()
        return None

#Receive from multicast and tries to connect with a client
def search_for_clients():

    global connection_buffer

    udp_socket = socket_for_multicast()

    # Try to connect with a client
    while (1):

        # wait for a client
        client_addr = receive_from_multicast(udp_socket)

        tcp_socket = establish_connection(client_addr)

        # Add the connection to buffer!
        if (tcp_socket is not None):
            connection_buffer_lock.acquire()
            connection_buffer.append(tcp_socket)
            connection_buffer_lock.release()

# Receive packets from connections (clients)
def receive_from_clients():

    global connection_buffer

    while(1):

        # take a copy of connections!
        connection_buffer_lock.acquire()
        clients = connection_buffer
        connection_buffer_lock.release()

        # receive over multiple sockets!
        if (len(clients)):
            readable,_,_  = select.select(clients, [], [])

            for sock in readable:
                data, addr = sock.recvfrom(1024)
                print 'Received data: %s' % data

#Spawn a thread to search for clients and to establish connection!
thread.start_new_thread(search_for_clients,())

#Spawn a thread to receive requests from clients
thread.start_new_thread(receive_from_clients,())

while(1):
    pass
