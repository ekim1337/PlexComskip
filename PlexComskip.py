#!/usr/bin/python

import configparser
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

# Config stuff.
pwd = os.path.dirname(os.path.realpath(__file__))
config_file_path = os.path.join(pwd, 'PlexComskip.conf')
if not os.path.exists(config_file_path):
  print(f'Config file not found: {config_file_path}')
  print('Make a copy of PlexConfig.conf.example named PlexConfig.conf, modify as necessary, and place in the same directory as this script.')
  sys.exit(1)

config = configparser.ConfigParser({
  'comskip-ini-path' : os.path.join(pwd, 'comskip.ini'),
  'temp-root' : tempfile.gettempdir(),
  'comskip-root' : tempfile.gettempdir(),
  'nice-level' : '0'
})
config.read(config_file_path)

COMSKIP_PATH = os.path.expandvars(os.path.expanduser(config.get('Helper Apps', 'comskip-path')))
COMSKIP_INI_PATH = os.path.expandvars(os.path.expanduser(config.get('Helper Apps', 'comskip-ini-path')))
FFMPEG_PATH = os.path.expandvars(os.path.expanduser(config.get('Helper Apps', 'ffmpeg-path')))
LOG_FILE_PATH = os.path.expandvars(os.path.expanduser(config.get('Logging', 'logfile-path')))
CONSOLE_LOGGING = config.getboolean('Logging', 'console-logging')
TEMP_ROOT = os.path.expandvars(os.path.expanduser(config.get('File Manipulation', 'temp-root')))
COMSKIP_ROOT = os.path.expandvars(os.path.expanduser(config.get('File Manipulation', 'comskip-root')))
COPY_ORIGINAL = config.getboolean('File Manipulation', 'copy-original')
SAVE_ALWAYS = config.getboolean('File Manipulation', 'save-always')
SAVE_FORENSICS = config.getboolean('File Manipulation', 'save-forensics')
NICE_LEVEL = config.get('Helper Apps', 'nice-level')
CONVERT = config.getboolean('File Manipulation', 'convert-to-mp4')
KEEP_ORIGINAL = config.getboolean('File Manipulation', 'keep-original')

# Exit states
CONVERSION_SUCCESS = 0
CONVERSION_DID_NOT_MODIFY_ORIGINAL = 1
CONVERSION_SANITY_CHECK_FAILED = 2
EXCEPTION_HANDLED = 3
COMSKIP_FAILED = 4

# Logging.
session_uuid = str(uuid.uuid4())
fmt = f'%%(asctime)-15s [{session_uuid[:6]}] %%(message)s'
if not os.path.exists(os.path.dirname(LOG_FILE_PATH)):
  os.makedirs(os.path.dirname(LOG_FILE_PATH))
logging.basicConfig(level=logging.INFO, format=fmt, filename=LOG_FILE_PATH)
if CONSOLE_LOGGING:
  console = logging.StreamHandler()
  console.setLevel(logging.INFO)
  formatter = logging.Formatter('%(message)s')
  console.setFormatter(formatter)
  logging.getLogger('').addHandler(console)

# Human-readable bytes.
def sizeof_fmt(num, suffix='B'):

  for unit in ['','K','M','G','T','P','E','Z']:
    if abs(num) < 1024.0:
        return f"{num:3.1f}{unit}{suffix}"
    num /= 1024.0
  return f"{num:.1f}Y{suffix}"

if len(sys.argv) < 2:
  print('Usage: PlexComskip.py input-file.mkv')
  sys.exit(1)

# Clean up after ourselves and exit.
def cleanup_and_exit(temp_dir, keep_temp=False, exit_code=CONVERSION_SUCCESS, **kwargs):
  if keep_temp:
    logging.info(f'Leaving temp files in: {temp_dir}, {comskip_out}')
  else:
    try:
      os.chdir(os.path.expanduser('~'))  # Get out of the temp dir before we nuke it (causes issues on NTFS)
      shutil.rmtree(temp_dir)
      if temp_dir != comskip_out:
        shutil.rmtree(comskip_out)
    except Exception as e:
      logging.error('Problem whacking temp dirs: {temp_dir}, {comskip_out}')
      logging.error(str(e))
      exit_code=EXCEPTION_HANDLED
  if not KEEP_ORIGINAL:
    try:
      orig_file = kwargs.get('original_file')
      os.remove(orig_file)
      logging.info(f'{orig_file} removed per the setting in the PlexComskip.conf file "keep-original: False"')
    except:
      logging.error(f'Unable to remove file: {orig_file}')
      exit_code=EXCEPTION_HANDLED

  # Exit cleanly.
  logging.info('Done processing!')
  sys.exit(exit_code)

# If we're in a git repo, let's see if we can report our sha.
logging.info(f'PlexComskip got invoked from {pwd}')
try:
  git_sha = subprocess.check_output('git rev-parse --short HEAD', shell=True)
  if git_sha:
    logging.info(f'Using version: {git_sha.strip()}')
except: pass

# Set our own nice level and tee up some args for subprocesses (unix-like OSes only).
NICE_ARGS = []
if sys.platform != 'win32':
  try:
    nice_int = max(min(int(NICE_LEVEL), 20), 0)
    if nice_int > 0:
      os.nice(nice_int)
      NICE_ARGS = ['nice', '-n', str(nice_int)]
  except Exception as e:
    logging.error(f'Couldn\'t set nice level to {NICE_LEVEL}: {e}')

# On to the actual work.
try:
  video_path = os.path.abspath(sys.argv[1])
  if not os.path.exists(video_path):
      logging.error(f'{video_path} does not exist.  Please check the input file.')
      raise Exception('input file does not exist')
  temp_dir = os.path.join(TEMP_ROOT, session_uuid)
  comskip_out = os.path.join(COMSKIP_ROOT, session_uuid)
  os.makedirs(temp_dir)
  if temp_dir != comskip_out:
    os.makedirs(comskip_out)
  os.chdir(temp_dir)

  logging.info(f'Using session ID: {session_uuid}')
  logging.info(f'Using temp dir: {temp_dir}')
  logging.info(f'Using comskip dir: {comskip_out}')
  logging.info(f'Using input file: {video_path}')

  output_video_dir = os.path.dirname(video_path)
  if sys.argv[2:]:
    logging.info(f'Output will be put in: {sys.argv[2]}')
    output_video_dir = os.path.dirname(sys.argv[2])

  video_basename = os.path.basename(video_path)
  video_name, video_ext = os.path.splitext(video_basename)

except Exception as e:
  logging.error(f'Something went wrong setting up temp paths and working files: {e}')
  sys.exit(EXCEPTION_HANDLED)

try:
  if COPY_ORIGINAL or SAVE_ALWAYS: 
    video_source_file = os.path.join(temp_dir, video_basename)
    logging.info(f'Copying file to work on it: {video_source_file}')
    shutil.copy(video_path, temp_dir)
  else:
    video_source_file = video_path

  # Process with comskip.
  cmd = NICE_ARGS + [COMSKIP_PATH, '--output', comskip_out, '--ini', COMSKIP_INI_PATH, video_source_file]
  logging.info(f'[comskip] Command: {cmd}')
  comskip_status = subprocess.call(cmd)
  if comskip_status != 0:
    logging.error(f'Comskip did not exit properly with code: {comskip_status}')
    cleanup_and_exit(temp_dir, False, COMSKIP_FAILED)
    #raise Exception('Comskip did not exit properly')

except Exception as e:
  logging.error(f'Something went wrong during comskip analysis: {e}')
  cleanup_and_exit(temp_dir, SAVE_ALWAYS or SAVE_FORENSICS, EXCEPTION_HANDLED)

edl_file = os.path.join(comskip_out, video_name + '.edl')
logging.info('Using EDL: ' + edl_file)
try:
  segments = []
  prev_segment_end = 0.0
  if os.path.exists(edl_file):
    with open(edl_file, 'rb') as edl:
      
      # EDL contains segments we need to drop, so chain those together into segments to keep.
      for segment in edl:
        start, end, something = segment.split()
        if float(start) == 0.0:
          logging.info('Start of file is junk, skipping this segment...')
        else:
          keep_segment = [float(prev_segment_end), float(start)]
          logging.info(f'Keeping segment from {keep_segment[0]} to {keep_segment[1]}...')
          segments.append(keep_segment)
        prev_segment_end = end

  # Write the final keep segment from the end of the last commercial break to the end of the file.
  keep_segment = [float(prev_segment_end), -1]
  logging.info(f'Keeping segment from {float(prev_segment_end)} to the end of the file...')
  segments.append(keep_segment)

  segment_files = []
  segment_list_file_path = os.path.join(temp_dir, 'segments.txt')
  with open(segment_list_file_path, 'wb') as segment_list_file:
    for i, segment in enumerate(segments):
      segment_name = f'segment-{i}'
      segment_file_name = f'{segment_name}{video_ext}'
      if segment[1] == -1:
        duration_args = []
      else:
        duration_args = ['-t', str(segment[1] - segment[0])]
      cmd = NICE_ARGS + [FFMPEG_PATH, '-i', video_source_file, '-ss', str(segment[0])]
      cmd.extend(duration_args)
      # cmd.extend(['-c', 'copy', segment_file_name])
      cmd.extend([segment_file_name])
      logging.info(f'[ffmpeg] Command: {cmd}')
      try:
        subprocess.call(cmd)
      except Exception as e:
        logging.error(f'Exception running ffmpeg: {e}')
        cleanup_and_exit(temp_dir, SAVE_ALWAYS or SAVE_FORENSICS, EXCEPTION_HANDLED)
      
      # If the last drop segment ended at the end of the file, we will have written a zero-duration file.
      if os.path.exists(segment_file_name):
        if os.path.getsize(segment_file_name) < 1000:
          logging.info(f'Last segment ran to the end of the file, not adding bogus segment {(i+1)} for concatenation.')
          continue

        segment_files.append(segment_file_name)
        segment_list_file.write(f'file {segment_file_name}\n'.encode('utf-8'))

except Exception as e:
  logging.error(f'Something went wrong during splitting: {e}'.encode('utf-8'))
  cleanup_and_exit(temp_dir, SAVE_ALWAYS or SAVE_FORENSICS, EXCEPTION_HANDLED)

logging.info(f'Going to concatenate {len(segment_files)} files from the segment list.')
try:
  if CONVERT:
    video_basename = os.path.splitext(video_basename)[0] + '.mp4'
    cmd = NICE_ARGS + [FFMPEG_PATH, '-y', '-f', 'concat', '-i', segment_list_file_path, '-c:v', 'libx264', os.path.join(temp_dir, video_basename)]
  else:
    cmd = NICE_ARGS + [FFMPEG_PATH, '-y', '-f', 'concat', '-i', segment_list_file_path, '-c', 'copy', os.path.join(temp_dir, video_basename)]
  logging.info(f'[ffmpeg] Command: {cmd}')
  subprocess.call(cmd)

except Exception as e:
  logging.error(f'Something went wrong during concatenation: {e}')
  cleanup_and_exit(temp_dir, SAVE_ALWAYS or SAVE_FORENSICS, EXCEPTION_HANDLED)

try:
  if CONVERT or KEEP_ORIGINAL:
    logging.info(f'Sanity check skipped when files are converted or original is saved.')
    # convert will be creating a new file name so no need to preserve the original
    if CONVERT:
      logging.info(f'Copying the output file into place: {video_basename} -> {output_video_dir}')
      shutil.copy(os.path.join(temp_dir, video_basename), output_video_dir)
    # not converting so we will be losing the original, if we don't give the new file a new name
    else:
      new_video_basename, extension = os.path.splitext(video_basename)
      new_video_basename += '.no-commercials' + extension
      source = os.path.join(temp_dir, video_basename)
      destination = os.path.join(output_video_dir, new_video_basename)
      logging.info(f'Copying the output file into place: {source} -> {destination}')
      shutil.copy(source, destination)

    cleanup_and_exit(temp_dir, SAVE_ALWAYS, original_file=video_path)
  else:
    logging.info('Sanity checking our work...')
    input_size = os.path.getsize(os.path.abspath(video_path))
    output_size = os.path.getsize(os.path.abspath(os.path.join(temp_dir, video_basename)))
    if input_size and 1.01 > float(output_size) / float(input_size) > 0.99:
      logging.info(f'Output file size was too similar (doesn\'t look like we did much); we won\'t replace the original: {sizeof_fmt(input_size)} -> {sizeof_fmt(output_size)}')
      logging.info('too little difference - suspect problem - override KEEP_ORIGINAL')
      KEEP_ORIGINAL = True
      cleanup_and_exit(temp_dir, SAVE_ALWAYS, CONVERSION_DID_NOT_MODIFY_ORIGINAL)
    elif input_size and 1.1 > float(output_size) / float(input_size) > 0.5:
      logging.info(f'Output file size looked sane, we\'ll replace the original: {sizeof_fmt(input_size)} -> {sizeof_fmt(output_size)}')
      logging.info(f'Copying the output file into place: {video_basename} -> {output_video_dir}')
      shutil.copy(os.path.join(temp_dir, video_basename), output_video_dir)
      cleanup_and_exit(temp_dir, SAVE_ALWAYS)
    else:
      logging.info(f'too much difference between before and after sizes- suspect problem - override KEEP_ORIGINAL')
      KEEP_ORIGINAL = True
      logging.info(f'Output file size looked wonky (too big or too small); we won\'t replace the original: {sizeof_fmt(input_size)} -> {sizeof_fmt(output_size)}')
      cleanup_and_exit(temp_dir, SAVE_ALWAYS or SAVE_FORENSICS, CONVERSION_SANITY_CHECK_FAILED)
except Exception as e:
  logging.error(f'Something went wrong during sanity check: {e}')
  cleanup_and_exit(temp_dir, SAVE_ALWAYS or SAVE_FORENSICS, EXCEPTION_HANDLED)
