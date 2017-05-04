# /usr/bin/env python3
import shlex
import subprocess
import requests
import shutil
import os
import hashlib
import time
import gzip
import queue

#API conf
base_url = 'http://inlovewith.space/dev/web'
get_work_url = base_url + '?get_job'
put_work_url = base_url + '?put_job'

#Hashcat conf
hashcat = 'hashcat64.exe'
performance = '-w 3'
outfile = 'pass.key'

#Folders
dict_folder = 'dicts/'
hccap_folder = 'hccap/'

"""
    download_file will download file with given [url] and [filename]
    -
    opens stream and if response code == 200 pipe it to shutil.copyfileobj()
    -
    returns 0 when OK
            1 when url or filename not specified
            2 not 200 response code
            3 in other cases
"""

# TODO: add checks and exeptions cather
def download_file(url=None, filename=None):
    if url == None or filename == None:
        return 1
    try:
        r = requests.get(url, stream=True)
        total_size = int(r.headers.get('content-length'))
        if r.status_code == 200:
            with open(filename, 'wb')as f:
                #shutil.copyfileobj(r.raw, f)
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk:  # filter out keep-alive new chunks
                        f.write(chunk)
            f.close()
            r.close()
            return 0
        else:
            return 2
    except Exception as e:
        print("Exeption: {}".format(e))
        return 3

# Ungzip [input] to [output]
def ungzip(input, output):
    with gzip.open(input, 'rb') as f_in:
        with open(output, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

# Returns sha256(filename)
def calc_sha256(filename, block_size=256 * 128):
    h = hashlib.sha256()
    with open(filename, 'rb') as f:
        for chunk in iter(lambda: f.read(block_size), b''):
            h.update(chunk)
    return h.hexdigest()

# Returns 1 if hash(filename) == [hashsum], 0 if not
def check_hash(filename, hashsum):
    if calc_sha256(filename) == hashsum:
        return 1
    else:
        return 0

# Get job from server: request ot get_work_url and return content in json format
def get_job():
    try:
        r = requests.get(get_work_url)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("Failed to get job")
        exit(1)

# Send [content] to put_work_url
def put_job(content):
    r = requests.post(put_work_url, json=content)
    if r.status_code == 200:
        return 1
    else:
        return 0


job = {}

#Check folders
if not os.path.exists(dict_folder):
    os.makedirs(dict_folder)
if not os.path.exists(hccap_folder):
    os.makedirs(hccap_folder)


while True:
    dict_queue = queue.deque()  # Queue for dictionaries
    handshake = ""



    # prepare job
    if len(job) == 0:
        job = get_job()
        print(job)
        if job['id'] == -1:
            print("No tasks. Nothing to do.")
            exit(1)

        # Download handshake and check hashsum
        handshake = "".join((hccap_folder + job['name'] + ".hccap").split(' '))
        download_file(job['url'], handshake)
        if not check_hash(handshake, job['hash']):
            print("[ERROR] Checksums do not match")
            exit(1)

        # Downaload all dicts and check hashsums
        for i in job['dicts']:
            filename = dict_folder + i['dict_url'].split('/')[-1]
            if not os.path.exists(filename):
                print('Downloading {}'.format(filename))
                download_file(i['dict_url'], filename)
                if not check_hash(filename, i['dict_hash']):
                    print("[ERROR] Checksums do not match. Exiting...")
                    exit(1)
            else:
                if not check_hash(filename, i['dict_hash']):
                    print('Downloading {}'.format(filename))
                    download_file(i['dict_url'], filename)
                    if not check_hash(filename, i['dict_hash']):
                        print("[ERROR] Checksums do not match. Exiting...")
                        exit(1)

            # Unpack dictionaries if necessary
            if filename[-3:] == '.gz':
                if not os.path.exists(''.join([i + '.' for i in filename.split('.')[:-1]])[:-1]):
                    print("Unpacking {}".format(filename))
                    ungzip(filename, ''.join([i + '.' for i in filename.split('.')[:-1]])[:-1])
                    filename = ''.join([i + '.' for i in filename.split('.')[:-1]])[:-1]
                dict_queue.append((filename[:-3], i['dict_id']))
            else:
                dict_queue.append((filename, i['dict_id']))

    # run hashcat for every dict
    while len(dict_queue):
        i = dict_queue.popleft()  # i = current dictionary
        filename = i[0]
        dict_id = i[1]

        try:
            cracker = '{0} -m2500 --potfile-disable --outfile-format=2 {1} -o {2} {3} {4}'.format(hashcat,
                                                                                                  performance,
                                                                                                  outfile,
                                                                                                  handshake,
                                                                                                  filename)

            # Send status to api
            put_job({"status_job": "started",
                     "task_id": job['id'],
                     "dict_id": dict_id})
            #Run hashcat with arguments
            subprocess.check_call(shlex.split(cracker))

        #Catch exceptions
        except subprocess.CalledProcessError as ex:
            if ex.returncode == -2:
                print('[WARNING] Thermal watchdog barked')
                print('Sleeping...')
                dict_queue.appendleft(i)
                time.sleep(120)
                continue
            if ex.returncode == -1:
                print('Internal error')
                exit(1)
            if ex.returncode == 1:
                print('[INFO] Exausted.')
            if ex.returncode == 2:
                print('User abort')
                exit(1)
            if ex.returncode not in [-2, -1, 1, 2]:
                print('Cracker {0} died with code {1}'.format(hashcat, ex.returncode))
                print('Check you have CUDA/OpenCL support')
                exit(1)
        except KeyboardInterrupt as ex:
            print('\nKeyboard interrupt. Quiting...')
            #Cleanup
            if os.path.exists(outfile):
                os.unlink(outfile)
                exit(1)


        if os.path.exists(outfile): #If bruted
            k = open(outfile, 'r')
            key = k.readline()
            k.close()
            key = key.rstrip('\n')
            if len(key) >= 8:
                print('Key found for job {0}:{1}'.format(job['name'], key))
                while not put_job({'job_status':  'finished', #Send key to server
                                   'task_id':      job['id'],
                                   'dict_id':        dict_id,
                                   'task_status':        '2',
                                   'dict_status':        '1',
                                   'net_key':            key}):
                    print("Can't submit key")
                    time.sleep(20)
                break
            else:
                print("Key for task {0} not found in {1} :(".format(job['name'], filename))
                while not put_job({'job_status': 'finished',  # Send fail status
                                   'task_id': job['id'],
                                   'dict_id': dict_id,
                                   'task_status': '3',
                                   'dict_status': '1',
                                   'net_key': ""}):
                    print("Can't data to server")
        else:
            print("Key for task {0} not found in {1} :(".format(job['name'], filename))
            while not put_job({'job_status':     'finished', #Send fail status
                               'task_id':      job['id'],
                               'dict_id':        dict_id,
                               'task_status':        '3',
                               'dict_status':        '1',
                               'net_key':           ""}):
                print("Can't data to server")

        # cleanup
        if os.path.exists(outfile):
            os.unlink(outfile)
    #reset job
    print("Going to next job")
    job = {}
    time.sleep(5)
