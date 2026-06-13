#!/bin/bash
# Upload a PDF to reMarkable via rmapi
# Usage: upload_to_remarkable.sh <pdf_file> [remote_folder] [rmapi_bin]

set -e

PDF_FILE="$1"
REMOTE_FOLDER="${2:-GTD Daily}"
RMAPI="${3:-rmapi}"

if [ -z "$PDF_FILE" ]; then
    echo "Usage: $0 <pdf_file> [remote_folder] [rmapi_bin]"
    exit 1
fi

if [ ! -f "$PDF_FILE" ]; then
    echo "Error: File not found: $PDF_FILE"
    exit 1
fi

# Ensure folder exists
$RMAPI -ni mkdir "$REMOTE_FOLDER" 2>/dev/null || true

# Upload
echo "Uploading $(basename "$PDF_FILE") to $REMOTE_FOLDER..."
$RMAPI -ni put "$PDF_FILE" "$REMOTE_FOLDER/"
echo "Done."
