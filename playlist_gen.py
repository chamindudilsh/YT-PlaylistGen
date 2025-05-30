import os
import re
import sys
import logging
import traceback
from datetime import datetime
import google_auth_oauthlib.flow
import google.auth.transport.requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

# --- Configuration ---
YT_LINKS_FILE = 'yt_links.txt'
DEFAULT_PLAYLIST_TITLE = "My New Playlist"
DEFAULT_PLAYLIST_DESCRIPTION = "Playlist created automatically using a Python script."
PLAYLIST_TITLE = input(f"Enter the playlist name (default: '{DEFAULT_PLAYLIST_TITLE}'): ").strip()
PLAYLIST_DESCRIPTION = input(f"Enter the playlist description (default: '{DEFAULT_PLAYLIST_DESCRIPTION}'): ").strip()
LOG_FILE_NAME = "logs.txt"

# The privacy status of the playlist ('public', 'private', or 'unlisted').
PLAYLIST_PRIVACY_STATUS = 'unlisted'

# OAuth 2.0 scopes required for creating playlists and adding items.
SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
# IMPORTANT: This is the name of the file where your OAuth 2.0 client secrets are stored.
# You MUST download this JSON file from your Google Cloud Console (after creating an OAuth 2.0 Client ID for Desktop app)
# and rename it to 'client_secrets.json'. Place it in the same directory as this script.
CLIENT_SECRETS_FILE = 'config/client_secrets.json'
# The name of the file where the user's credentials will be stored after first authentication.
CREDENTIALS_FILE = 'config/credentials.json'

if not PLAYLIST_TITLE:
    PLAYLIST_TITLE = DEFAULT_PLAYLIST_TITLE

if not PLAYLIST_DESCRIPTION:
    PLAYLIST_DESCRIPTION = DEFAULT_PLAYLIST_DESCRIPTION

# --- Helper Functions ---

def get_authenticated_service():
    """
    Authenticates with the YouTube Data API using OAuth 2.0.
    It will try to load existing credentials from 'credentials.json'.
    If not found or expired, it will initiate a new OAuth 2.0 flow using 'client_secrets.json',
    which will open a browser for the user to authenticate.
    New credentials will then be saved to 'credentials.json'.
    """
    credentials = None

    # Check if credentials file exists
    if os.path.exists(CREDENTIALS_FILE):
        try:
            credentials = Credentials.from_authorized_user_file(CREDENTIALS_FILE, SCOPES)
        except Exception as e:
            print(f"Error loading credentials from {CREDENTIALS_FILE}: {e}")
            credentials = None

    # If no valid credentials, initiate the OAuth 2.0 flow
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            print("Refreshing access token...")
            try:
                credentials.refresh(google.auth.transport.requests.Request())
            except Exception as e:
                print(f"Error refreshing token: {e}")
                credentials = None
        
        if not credentials or not credentials.valid:
            print("Initiating new authentication flow...")
            # This is where client_secrets.json is used to start the authentication process.
            if not os.path.exists(CLIENT_SECRETS_FILE):
                print(f"Error: '{CLIENT_SECRETS_FILE}' not found.")
                print("Please download your OAuth 2.0 client secrets JSON file from Google Cloud Console")
                print("and rename it to 'client_secrets.json' in the same directory as this script.")
                return None

            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES)
            
            # Run the local server flow to get credentials
            # This will open a browser window for authentication
            try:
                credentials = flow.run_local_server(port=0)
            except Exception as e:
                print(f"Error during OAuth 2.0 flow: {e}")
                print("Please ensure 'client_secrets.json' is correctly configured and you have an internet connection.")
                return None

        # Save the credentials for future use
        with open(CREDENTIALS_FILE, 'w') as token:
            token.write(credentials.to_json())
        print(f"Credentials saved to {CREDENTIALS_FILE}")

    return build('youtube', 'v3', credentials=credentials)

def extract_video_id(url):
    """
    Extracts the YouTube video ID from a given URL.
    Supports various YouTube URL formats.
    """
    # Regex for standard YouTube watch URLs
    match = re.search(r'(?:v=|youtu\.be\/|embed\/|v\/|watch\?v=|\/videos\/)([a-zA-Z0-9_-]{11})', url)
    if match:
        return match.group(1)
    # Regex for YouTube Shorts URLs
    match = re.search(r'shorts\/([a-zA-Z0-9_-]{11})', url)
    if match:
        return match.group(1)
    return None

def create_playlist(youtube_service, title, description, privacy_status):
    """
    Creates a new YouTube playlist.
    Returns the ID of the newly created playlist.
    """
    print(f"Attempting to create playlist: '{title}'...")
    request_body = {
        'snippet': {
            'title': title,
            'description': description
        },
        'status': {
            'privacyStatus': privacy_status
        }
    }
    try:
        response = youtube_service.playlists().insert(
            part='snippet,status',
            body=request_body
        ).execute()
        playlist_id = response['id']
        print(f"Playlist '{title}' created successfully! ID: {playlist_id}")
        return playlist_id
    except HttpError as e:
        print(f"An HTTP error {e.resp.status} occurred while creating playlist: {e.content.decode()}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while creating playlist: {e}")
        return None

def add_video_to_playlist(youtube_service, playlist_id, video_id):
    """
    Adds a video to the specified playlist.
    """
    print(f"Adding video ID '{video_id}' to playlist '{playlist_id}'...")
    request_body = {
        'snippet': {
            'playlistId': playlist_id,
            'resourceId': {
                'kind': 'youtube#video',
                'videoId': video_id
            }
        }
    }
    try:
        youtube_service.playlistItems().insert(
            part='snippet',
            body=request_body
        ).execute()
        print(f"Successfully added video ID '{video_id}'.")
        return True
    except HttpError as e:
        # Check if the error is due to the video not being found or being private/deleted
        if e.resp.status == 404:
            print(f"Warning: Video ID '{video_id}' not found or is private/deleted. Skipping.")
        elif e.resp.status == 400 and "duplicate" in e.content.decode().lower():
            print(f"Warning: Video ID '{video_id}' is already in the playlist. Skipping.")
        else:
            print(f"An HTTP error {e.resp.status} occurred while adding video '{video_id}': {e.content.decode()}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred while adding video '{video_id}': {e}")
        return False

# --- Main Script Logic ---

def main():
    # 1. Read YouTube links from the file
    video_ids = []
    if not os.path.exists(YT_LINKS_FILE):
        print(f"Error: '{YT_LINKS_FILE}' not found in the current directory.")
        print("Please create this file and add YouTube video URLs, one per line.")
        return

    with open(YT_LINKS_FILE, 'r') as f:
        for line in f:
            url = line.strip()
            if url:
                video_id = extract_video_id(url)
                if video_id:
                    video_ids.append(video_id)
                else:
                    print(f"Warning: Could not extract video ID from URL: {url}. Skipping.")

    if not video_ids:
        print(f"No valid YouTube video links found in '{YT_LINKS_FILE}'. Exiting.")
        return

    print(f"Found {len(video_ids)} video IDs to process.")

    # 2. Authenticate with YouTube API. This function uses 'client_secrets.json'.
    youtube = get_authenticated_service()
    if not youtube:
        print("Authentication failed. Exiting.")
        return

    # 3. Create the new playlist
    playlist_id = create_playlist(youtube, PLAYLIST_TITLE, PLAYLIST_DESCRIPTION, PLAYLIST_PRIVACY_STATUS)
    if not playlist_id:
        print("Failed to create playlist. Exiting.")
        return

    # 4. Add videos to the playlist
    print("\nStarting to add videos to the playlist...")
    added_count = 0
    for video_id in video_ids:
        if add_video_to_playlist(youtube, playlist_id, video_id):
            added_count += 1
    
    print(f"\nFinished processing. Added {added_count} out of {len(video_ids)} videos to the playlist.")
    print(f"You can view your playlist here: https://www.youtube.com/playlist?list={playlist_id}")

if __name__ == '__main__':
    # Configure the logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Create a file handler that logs ERROR and CRITICAL messages
    file_handler = logging.FileHandler(LOG_FILE_NAME, mode='a')
    file_handler.setLevel(logging.ERROR)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    # This allows you to see regular output on the console while errors go to file
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        print("Starting script execution...")
        main()
        print(f"Script execution completed successfully. Error logs (if any) saved to {LOG_FILE_NAME}!")

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        print(f"\n--- SCRIPT ERROR ---\nAn unexpected error occurred during execution. Please check '{LOG_FILE_NAME}' for details.", file=sys.stderr)

    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
