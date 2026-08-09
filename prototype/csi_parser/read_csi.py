import os
import sys
import argparse
import json
import csv
import serial
from datetime import datetime, UTC
from io import StringIO

DATA_COLUMNS_NAMES_C5C6 = ['type', 'id', 'mac', 'rssi', 'rate','noise_floor','fft_gain','agc_gain', 'channel', 'local_timestamp',  'sig_len', 'rx_state', 'len', 'first_word', 'data']
DATA_COLUMNS_NAMES = ['type', 'id', 'mac', 'rssi', 'rate', 'sig_mode', 'mcs', 'bandwidth', 'smoothing', 'not_sounding', 'aggregation', 'stbc', 'fec_coding',
                      'sgi', 'noise_floor', 'ampdu_cnt', 'channel', 'secondary_channel', 'local_timestamp', 'ant', 'sig_len', 'rx_state', 'len', 'first_word', 'data']
UNIX_START_TIME = None
OFFSET_TIME = None
TIMESTAMP_IDX = None

def csi_data_to_csv(port: str, csv_writer, log_file_fd):
    global fft_gains, agc_gains, UNIX_START_TIME, OFFSET_TIME, TIMESTAMP_IDX
    set = serial.Serial(port=port, baudrate=921600,bytesize=8, parity='N', stopbits=1)
    set.flush()
    if set.isOpen():
        print('open success')
    else:
        print('open failed')
        return
    
    UNIX_START_TIME = int(datetime.now().timestamp())
    while True:
        strings = str(set.readline())
        if not strings:
            break
        strings = strings.lstrip('b\'').rstrip('\\r\\n\'')
        index = strings.find('CSI_DATA')

        if index == -1:
            log_file_fd.write(strings + '\n')
            log_file_fd.flush()
            continue

        try:
            csv_reader = csv.reader(StringIO(strings))
            csi_data = next(csv_reader)
            csi_data_len = int (csi_data[-3])
            if len(csi_data) != len(DATA_COLUMNS_NAMES) and len(csi_data) != len(DATA_COLUMNS_NAMES_C5C6):
                print('element number is not equal',len(csi_data),len(DATA_COLUMNS_NAMES) )
                # print(csi_data)
                log_file_fd.write('element number is not equal\n')
                log_file_fd.write(strings + '\n')
                log_file_fd.flush()
                continue
        except:
            continue

        try:
            csi_raw_data = json.loads(csi_data[-1])
        except json.JSONDecodeError:
            print('data is incomplete')
            log_file_fd.write('data is incomplete\n')
            log_file_fd.write(strings + '\n')
            log_file_fd.flush()
            continue
        if csi_data_len != len(csi_raw_data):
            print('csi_data_len is not equal',csi_data_len,len(csi_raw_data))
            log_file_fd.write('csi_data_len is not equal\n')
            log_file_fd.write(strings + '\n')
            log_file_fd.flush()
            continue
        
        if TIMESTAMP_IDX is None:
            if len(csi_data) == len(DATA_COLUMNS_NAMES):
                TIMESTAMP_IDX = 18
            else:
                TIMESTAMP_IDX = 9
            
            OFFSET_TIME = int (csi_data[TIMESTAMP_IDX])
            
        csi_data.append(UNIX_START_TIME + ((int (csi_data[TIMESTAMP_IDX]) - OFFSET_TIME) / 1_000_000.0))
        csv_writer.writerow(csi_data)
    
    set.close()
    return

def generate_csv_file_name():
    current_time = datetime.now(UTC)
    year = current_time.year
    month = current_time.month
    day = current_time.day
    hour = current_time.hour
    
    location = f'./csi_data/{year}/{month}/{day}/'
    os.makedirs(os.path.dirname(location), exist_ok=True)
    
    file_name = f'{year}-{month}-{day}_h{hour}.csv'
    
    return f'{location}{file_name}'

if __name__ == "__main__":
    if sys.version_info < (3, 6):
        print('Python version should be >= 3.6')
        exit()
        
    parser = argparse.ArgumentParser(
        description='Read CSI data from serial port and display it graphically')
    parser.add_argument('-p', '--port', dest='port', action='store', required=True,
                        help='Serial port number of csv_recv device')
    parser.add_argument('-s', '--store', dest='store_file', action='store', default=generate_csv_file_name(),
                        help='Save the data printed by the serial port to a file')
    parser.add_argument('-l', '--log', dest='log_file', action='store', default='./csi_data_log.txt',
                        help='Save other serial data the bad CSI data to a log file')
    
    args = parser.parse_args()
    serial_port = args.port
    file_name = args.store_file
    log_file_name = args.log_file
    
    file_exists = os.path.isfile(file_name)

    try:
        with open(file_name, 'a', newline='') as save_file_fd:
            csv_writer = csv.writer(save_file_fd)
            log_file_fd = open(log_file_name, 'w')
            
            if not file_exists:
                csv_writer.writerow([*DATA_COLUMNS_NAMES, 'timestamp_real'])
        
            csi_data_to_csv(serial_port, csv_writer, log_file_fd)
    except KeyboardInterrupt:
        log_file_fd.close()
        save_file_fd.close()
        
        os.replace(file_name, f"{file_name[:-4]}_{UNIX_START_TIME}-{int(datetime.now().timestamp())}.csv")
