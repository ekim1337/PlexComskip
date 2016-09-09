#!/usr/bin/python

import ConfigParser
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

# Config stuff.
config = ConfigParser.SafeConfigParser({'comskip-ini-path' : os.path.join(os.path.dirname(os.path.realpath(__file__)), 'comskip.ini'), 'temp-root' : tempfile.gettempdir()})
config.read(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'PlexComskip.conf'))

COMSKIP_PATH = os.path.expanduser(config.get('Helper Apps', 'comskip-path'))
COMSKIP_INI_PATH = os.path.expanduser(config.get('Helper Apps', 'comskip-ini-path'))
FFMPEG_PATH = os.path.expanduser(config.get('Helper Apps', 'ffmpeg-path'))
LOG_FILE_PATH = os.path.expanduser(config.get('Logging', 'logfile-path'))
CONSOLE_LOGGING = config.getboolean('Logging', 'console-logging')
TEMP_ROOT = os.path.expanduser(config.get('File Manipulation', 'temp-root'))
COPY_ORIGINAL = config.getboolean('File Manipulation', 'copy-original')
SAVE_ALWAYS = config.getboolean('File Manipulation', 'save-always')
SAVE_FORENSICS = config.getboolean('File Manipulation', 'save-forensics')

# Logging.
session_uuid = str(uuid.uuid4())
fmt = '%%(asctime)-15s [%s] %%(message)s' % session_uuid[:6]
logging.basicConfig(level=logging.INFO, format=fmt, filename=LOG_FILE_PATH)
if CONSOLE_LOGGING:
	console = logging.StreamHandler()
	console.setLevel(logging.INFO)
	formatter = logging.Formatter('%(message)s')
	console.setFormatter(formatter)
	logging.getLogger('').addHandler(console)

# This is a (brittle-as-hell) way to get direct access to the Part's files on different hosts that have the filesystem mounted in both places.
# It assumes you have only Show/Episode or Show/Season/Episode hierarchy directly under your library root dir.
# "in_path" is the path to the specific file given to us by PMS.
# "out_root" is the path to the locally-mounted section root.
#
def convert_path(in_path, out_root):

	head, ep = os.path.split(in_path)
	if 'season' in os.path.split(head)[1].lower():
		head, season = os.path.split(head)
	head, show = os.path.split(head)
	return os.path.join(out_root, show, ep)


# Human-readable bytes.
def sizeof_fmt(num, suffix='B'):

	for unit in ['','K','M','G','T','P','E','Z']:
		if abs(num) < 1024.0:
			return "%3.1f%s%s" % (num, unit, suffix)
		num /= 1024.0
	return "%.1f%s%s" % (num, 'Y', suffix)


if len(sys.argv) < 2:
	print 'Usage: PlexComskip.py input-file.mkv'
	sys.exit(1)

video_path = sys.argv[1]
temp_dir = os.path.join(TEMP_ROOT, session_uuid)
os.makedirs(temp_dir)
os.chdir(temp_dir)

original_video_dir = os.path.dirname(video_path)
video_basename = os.path.basename(video_path)
video_name, video_ext = os.path.splitext(video_basename)

if COPY_ORIGINAL or SAVE_ALWAYS: 
	temp_video_path = os.path.join(temp_dir, video_basename)
	logging.info('Copying file to work on it: %s' % temp_video_path)
	shutil.copy(video_path, temp_dir)
else:
	temp_video_path = video_path

# Process with comskip.
cmd = [COMSKIP_PATH, '--output', temp_dir, '--ini', COMSKIP_INI_PATH, temp_video_path]
logging.info('[comskip] Command: %s' % cmd)
subprocess.call(cmd)

edl_file = os.path.join(temp_dir, video_name + '.edl')
logging.info('Using EDL: ' + edl_file)

segments = []
prev_segment_end = 0.0
with open(edl_file, 'rb') as e:
	
	# EDL contains segments we need to drop, so chain those together into segments to keep.
	for segment in e:
		start, end, something = segment.split()
		if float(start) == 0.0:
			logging.info('Start of file is junk, skipping this segment...')
		else:
			keep_segment = [float(prev_segment_end), float(start)]
			logging.info('Keeping segment from %s to %s...' % (keep_segment[0], keep_segment[1]))
			segments.append(keep_segment)
		prev_segment_end = end

# Write the final keep segment from the end of the last commercial break to the end of the file.
keep_segment = [float(prev_segment_end), -1]
logging.info('Keeping segment from %s to the end of the file...' % prev_segment_end)
segments.append(keep_segment)

segment_files = []
segment_list_file_path = os.path.join(temp_dir, 'segments.txt')
with open(segment_list_file_path, 'wb') as segment_list_file:
	for i, segment in enumerate(segments):
		segment_name = 'segment-%s' % i
		segment_file_name = '%s%s' % (segment_name, video_ext)
		if segment[1] == -1:
			duration_args = []
		else:
			duration_args = ['-t', str(segment[1] - segment[0])]
		cmd = [FFMPEG_PATH, '-i', video_path, '-ss', str(segment[0])]
		cmd.extend(duration_args)
		cmd.extend(['-c', 'copy', segment_file_name])
		logging.info('[ffmpeg] Command: %s' % cmd)
		subprocess.call(cmd)
		
		# If the last drop segment ended at the end of the file, we will have written a zero-duration file.
		if os.path.getsize(segment_file_name) < 1000:
			logging.info('Last segment ran to the end of the file, not adding bogus segment %s for concatenation.' % (i + 1))
			continue

		segment_files.append(segment_file_name)
		segment_list_file.write('file %s\n' % segment_file_name)

logging.info('Going to concatenate %s files from the segment list.' % len(segment_files))

cmd = [FFMPEG_PATH, '-y', '-f', 'concat', '-i', segment_list_file_path, '-c', 'copy', os.path.join(temp_dir, video_basename)]
logging.info('[ffmpeg] Command: %s' % cmd)
subprocess.call(cmd)

logging.info('Sanity checking our work...')
success = False
cleanup = True
try:
	input_size = os.path.getsize(video_path)
	output_size = os.path.getsize(os.path.join(temp_dir, video_basename))
	if input_size and 1.1 > float(output_size) / float(input_size) > 0.5:
		logging.info('Output file size looked sane, we\'ll replace the original: %s -> %s' % (sizeof_fmt(input_size), sizeof_fmt(output_size)))
		logging.info('Copying the output file into place: %s -> %s' % (video_basename, original_video_dir))
		shutil.copy(os.path.join(temp_dir, video_basename), original_video_dir)
		success = True
	else:
		logging.info('Output file size looked wonky: %s -> %s' % (sizeof_fmt(input_size), sizeof_fmt(output_size)))
		raise
except:
	if SAVE_FORENSICS:
		logging.error('Looks like something went wrong, we\'ll leave the temp files around in %s for forensics.' % temp_dir)
		cleanup = False
	else:
		logging.error('Looks like something went wrong, we\'ll clean up after ourselves anyway (pro tip: set SAVE_FORENSICS = True to leave files in place).')

if SAVE_ALWAYS:
	logging.info('Leaving all files around for debugging in: %s' % temp_dir)
	sys.exit(0)

if cleanup:
	try:
		os.chdir(os.path.expanduser('~'))
		logging.info('Waiting a few seconds for... Windows stuff? ...and then whacking temp dir: %s' % temp_dir)
		time.sleep(5)
		shutil.rmtree(temp_dir)
	except Exception, e:
		logging.error('Problem whacking temp dir: %s' % e)

if success:	
	logging.info('All done, processing was successful!')
else:
	logging.info('All done, but processing failed. Left the original file in place.')