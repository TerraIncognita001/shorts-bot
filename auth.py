from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",  # comments + playlists
    ],
)
c = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("YT_CLIENT_ID=", c.client_id)
print("YT_CLIENT_SECRET=", c.client_secret)
print("YT_REFRESH_TOKEN=", c.refresh_token)
