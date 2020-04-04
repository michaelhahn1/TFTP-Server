import sys
import struct
import errno
from select import select
import socket
import random
from threading import Timer


#---------global variables-------#
timeout = 0
max_num_of_attempts = 0
#--------------------------------#

class SocketInformation:
    def __init__(self,socket,client_address,opcode,block_num,fd,packet_to_send):
        self.socket = socket
        self.client_address = client_address
        self.opcode = opcode
        self.block_num = block_num
        self.fd = fd
        self.packet_to_send = packet_to_send
        self.timer = None
        self.is_last_message = False
        self.expected_opcode = None
        self.last_send_packet = None
        self.retransmission_counter = 0
        self.is_next_read_from = False
        self.is_working = True
        self.last_received_packet = None
        self.timer = None

    def close_socket(self):
        if self.fd is not None:
            self.fd.close()
        self.socket.close()
        self.is_working = False



# --------------------Auxiliary Functions------------------------

def set_timer(sock):
    sock.timer = Timer(timeout, timeout_handler, (sock,))
    sock.timer.start()

def timeout_handler(sock):
    sock.retransmission_counter += 1
    if sock.retransmission_counter>max_num_of_attempts:
        sock.is_working = False
    sock.is_next_read_from = False


def sendPacket(packet, address, socket):
    socket.sendto(packet, address)

def readAndPackData(readF, i):
    data = readF.read(512)
    packet = struct.pack(">hh%ds" % len(data),3,i,data)
    return packet

def packERRMsg(errCode, errMsg):
    errMsgLen = len(errMsg)
    packet = struct.pack(">hh%dsB" % len(errMsg),5,errCode,errMsg.encode(),0)
    return packet

def awkPacket(i):
    packet = struct.pack(">hh",4,i)
    return packet
# --------------------------main function-------------------------------------#
def main():
    global timeout
    global max_num_of_attempts
    port = int(sys.argv[1]) # port number from user
    timeout = int(sys.argv[2]) # retransmition timeput in seconds
    max_num_of_attempts = int(sys.argv[3]) # number retransmition attempts allowed
    readable = []
    writable = []
    all_sockets_information_dictionary = {}
    last_client_address = None
    ignore_received_message = False
    try:
        listening_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # opening socket
        listening_socket.bind(('',port))
        while True:
            (readable, _, _) = select([listening_socket], [], [], 0)
            if listening_socket in readable: # make new connection if listening_socket is readable
                (msg_enc, client_address) = listening_socket.recvfrom(1024)
                if last_client_address is None:
                    last_client_address = client_address
                elif last_client_address == client_address:
                    ignore_received_message = True
                else:
                    ignore_received_message = False

                if not ignore_received_message:
                    random_port = random.randint(0, 65535) #this is the range of valid ports
                    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    client_socket.bind(('', random_port))
                    opcode = struct.unpack(">h", msg_enc[:2])[0] # getting the opcode
                    file_name =  msg_enc[2:msg_enc.index(b'\x00', 2, )].decode() # getting file name
                    mode = msg_enc[msg_enc.index(b'\x00', 2, ):-1].decode() # getting mode
                    block_num = -1 # in new connection initiating block # to -1
                    packet_to_send = None

                    if opcode == 1:  #read
                        try:
                            read_file = open(file_name, "rb")
                            block_num = 1
                        except OSError as error:
                            read_file = None
                            if error.errno == 13:
                                packet_to_send = packERRMsg(2, "Access violation.")
                            elif error.errno == 2:
                                packet_to_send = packERRMsg(1, "File not found.")
                            else:
                                packet_to_send = packERRMsg(0, error.strerror)
                        starting_socket = SocketInformation(client_socket,client_address,opcode,block_num,read_file,packet_to_send)

                    elif opcode == 2: #write
                        try:
                            write_file = open(file_name, "rb")
                            write_file.close()
                            packet_to_send = packERRMsg(6, "File already exists.")
                        except OSError as error:
                            if error.errno == 13:
                                packet_to_send =packERRMsg(2, "File already exists.")
                            try:
                                write_file = open(file_name, "wb")
                                block_num = 0
                            except OSError as error:
                                if error.errno == 12:
                                    packet_to_send = packERRMsg(3, "Disk full or allocation exceed.")
                                else:
                                    packet_to_send = packERRMsg(0, error.strerror)
                        if block_num==-1:
                            write_file = None
                        starting_socket = SocketInformation(client_socket, client_address, opcode, block_num, write_file, packet_to_send)
                    else:
                        packet_to_send = packERRMsg(4, "Illegal TFTP operation.")
                        starting_socket = SocketInformation(client_socket, client_address, opcode, block_num, None, packet_to_send)
                    all_sockets_information_dictionary[client_socket] = starting_socket

            skip = False
            for key in all_sockets_information_dictionary:
                if not all_sockets_information_dictionary[key].is_working:
                    skip = True
            if not skip:
                (readable, writeable, _) = select(list(all_sockets_information_dictionary.keys()),list(all_sockets_information_dictionary.keys()), [],0)

            for key in writeable:
                sock = all_sockets_information_dictionary[key]
                if not sock.is_working:
                    continue
                if sock.is_next_read_from:
                    continue
                if sock.is_last_message:
                    if sock.expected_opcode == 3:  #received last data packet, need to send one more ack
                        packet_to_send = awkPacket(sock.block_num)
                        sendPacket(packet_to_send, sock.client_address, sock.socket)
                    sock.close_socket()
                    continue
                if sock.block_num == -1:
                    sendPacket(sock.packet_to_send,sock.client_address,sock.socket)
                    sock.close_socket()
                    continue
                if sock.retransmission_counter > 0:
                    sendPacket(sock.last_send_packet,sock.client_address,sock.socket)
                    set_timer(sock)
                    sock.is_next_read_from = True
                    continue
                if sock.opcode == 1 or sock.opcode == 4:
                    packet_to_send = readAndPackData(sock.fd,sock.block_num)
                    sendPacket(packet_to_send,sock.client_address,sock.socket)
                    sock.last_send_packet = packet_to_send
                    sock.expected_opcode = 4
                    if len(packet_to_send) < 516: # check if this was the last DATA packet
                        sock.is_last_message = True
                    set_timer(sock)

                if sock.opcode == 3 or sock.opcode==2:
                    packet_to_send = awkPacket(sock.block_num)
                    sendPacket(packet_to_send,sock.client_address,sock.socket)
                    sock.last_send_packet = packet_to_send
                    sock.expected_opcode = 3
                    if sock.is_last_message == True:
                        sock.close_socket()
                        continue
                    set_timer(sock)
                sock.is_next_read_from = True

            for key in readable:
                sock = all_sockets_information_dictionary[key]
                if not sock.is_next_read_from:
                    continue
                (msg_enc, address) = sock.socket.recvfrom(516)
                if sock.last_received_packet is None :
                   sock.last_received_packet = msg_enc
                elif sock.last_received_packet == msg_enc:
                    continue
                sock.last_received_packet = msg_enc
                sock.timer.cancel()
                sock.retransmission_counter = 0
                opcode = struct.unpack(">h", msg_enc[:2])[0]  # getting the opcode
                #file_name = msg_enc[2:msg_enc.index(b'\x00', 2, )].decode()  # getting file name
                #mode = msg_enc[msg_enc.index(b'\x00', 2, ):-1].decode()  # getting mode
                if address != sock.client_address:  # got a msg from unknown user
                    packet_to_send = packERRMsg(5, "Unknown transfer ID.")
                    sendPacket(packet_to_send, sock.client_address, sock.socket)
                    sock.last_send_packet = packet_to_send
                    set_timer(sock)
                    sock.is_next_read_from = True
                    continue  # waiting for msg from known user

                if sock.expected_opcode == 4:
                    # --- handeling the opCode ---
                    if opcode == 4: # pacet received is AWK packet
                        if struct.unpack(">h", msg_enc[2:])[0] != sock.block_num:
                            packet_to_send = packERRMsg(1, "AWK index doesn't match DATA block #.")
                            sendPacket(packet_to_send,sock.client_address,sock.socket)
                            sock.close_socket()
                            continue
                        sock.block_num += 1
                    elif opcode == 5: # packet received is ERR packet
                        if len(msg_enc) > 5:
                            print(msg_enc[4:-1].decode()) # printing error msg
                        sock.close_socket()
                        continue
                    else:
                        packet_to_send = packERRMsg(4, "Illegal TFTP operation.")
                        sendPacket(packet_to_send, sock.client_address, sock.socket)
                        sock.close_socket()
                        continue

                if sock.expected_opcode == 3 :
                    if opcode == 3:  # DATA packet
                        if (struct.unpack(">h", msg_enc[2:4])[0]) != sock.block_num + 1:  # block # != current index
                            packet_to_send = packERRMsg(0, "AWK index doesn't match DATA block #.")
                            sendPacket(packet_to_send,sock.client_address,sock.socket)
                            sock.close_socket()
                            continue
                        # else - write data into file
                        data_write = msg_enc[4:]
                        try:
                            sock.fd.write(data_write)
                        except OSError as error:
                            if error.errno == 27:
                                packet_to_send = packERRMsg(3, "Disk full or allocation exceed.")
                            else:
                                packet_to_send = packERRMsg(0, error.strerror)
                            sendPacket(packet_to_send, sock.client_address, sock.socket)
                            sock.close_socket()
                            continue
                        sock.block_num += 1
                        if len(data_write) < 512:  # terminate the session
                            sock.is_last_message = True
                    elif opcode == 5:  # ERR packet
                        if len(msg_enc) > 5:
                            print(msg_enc[4:-1].decode())  # printing error msg
                        sock.close_socket()
                    else:  # the opCode is not correct
                        packet_to_send = packERRMsg(4, "Illegal TFTP operation.")
                        sendPacket(packet_to_send, sock.client_address, sock.socket)
                        sock.close_socket()
                sock.is_next_read_from = False
            updated_dictionary = {}
            for key in all_sockets_information_dictionary:
                sock = all_sockets_information_dictionary[key]
                if sock.is_working:
                    updated_dictionary[key] = sock
                else:
                    if sock.fd is not None:
                        sock.fd.close
                    if sock.socket is not None:
                        sock.socket.close

            all_sockets_information_dictionary = updated_dictionary
    except OSError as error:
        listening_socket.close()
        for sock in all_sockets_information_dictionary.values():
            if sock.is_working:
                sock.socket.close()
                sock.fd.close()

if __name__ == '__main__':
    main()