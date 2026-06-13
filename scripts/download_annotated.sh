#!/bin/bash
# Download latest annotated GTD PDF from reMarkable
# Usage: download_annotated.sh [remote_folder] [output_dir] [rmapi_bin]

set -e

REMOTE_FOLDER="${1:-GTD Daily}"
OUTPUT_DIR="${2:-/tmp/gtd_output}"
RMAPI="${3:-rmapi}"

mkdir -p "$OUTPUT_DIR"
cd "$OUTPUT_DIR"
rm -f *.rmdoc *.pdf *.png

# Find the latest GTD PDF in the remote folder
echo "Listing remote folder: $REMOTE_FOLDER"
LATEST_FILE=$($RMAPI -ni ls "$REMOTE_FOLDER" | grep "gtd" | sort | tail -1 | awk '{print $2}')

if [ -z "$LATEST_FILE" ]; then
    echo "No GTD file found on reMarkable."
    exit 1
fi

echo "Found latest file: $LATEST_FILE"

# Download the rmdoc (contains PDF + .rm annotation files)
echo "Downloading..."
$RMAPI -ni get "$REMOTE_FOLDER/$LATEST_FILE"

RMDOC_FILE=$(ls -t *.rmdoc 2>/dev/null | head -1)
if [ -z "$RMDOC_FILE" ]; then
    echo "Error: No rmdoc file downloaded"
    exit 1
fi

echo "Downloaded: $RMDOC_FILE"
