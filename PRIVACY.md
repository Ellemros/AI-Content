# Privacy Policy: Money Minds Content Tool

Last updated: 15 November 2026

The Money Minds Content Tool (this repository) is an internal, self-hosted tool. The owner of the Money Minds channel uses it to produce original educational videos and upload them to that channel. It has no other users and offers no public sign-up.

## What data the tool accesses

When the channel owner connects their YouTube channel, the tool requests these Google OAuth scopes:

| Scope | Purpose |
|---|---|
| `youtube.upload` | Upload the owner's own approved videos to the owner's own channel. |
| `youtube.readonly` | Read the owner's channel and video details, to confirm an upload and avoid duplicates. |
| `yt-analytics.readonly` | Read view counts and watch time for the owner's own videos. |
| `yt-analytics-monetary.readonly` | Read estimated revenue for the owner's own videos, to compare production cost with revenue. |

The tool does not access other people's channels, comments, subscribers' personal data, or any data of YouTube viewers beyond the aggregated analytics YouTube provides to the channel owner.

## How the data is stored

- OAuth tokens are stored only on the owner's computer, in a local folder (`data/secrets`) that is excluded from version control. They are never sent anywhere except to Google's own token endpoint.
- Aggregated analytics, such as views, watch time and estimated revenue per video, are stored in a local database on the owner's computer.
- Nothing is sold or shared with third parties. Nothing is used for advertising.

## Use of YouTube API Services

This tool uses YouTube API Services. By using it, the channel owner agrees to the [YouTube Terms of Service](https://www.youtube.com/t/terms). Google's handling of data is described in the [Google Privacy Policy](https://policies.google.com/privacy).

## Data retention and deletion

- Analytics are kept while the tool is in use.
- The owner can delete the local database and token files at any time.
- Access can also be revoked at any time at <https://myaccount.google.com/permissions>. After revocation the tool can no longer access the channel.

## Contact

Questions about this policy can be raised as an issue in this repository: <https://github.com/Ellemros/AI-Content/issues>
