"""Seeds the local Frappe Helpdesk dev instance with synthetic tickets.

Creates three groups, per CONTEXT.md's domain model:
  - Duplicate Clusters: sets of Reusable Tickets sharing Root-Cause Similarity,
    reworded per member, used as eval ground truth. Each cluster has four
    "standard" members plus one "low-lexical-overlap" member -- same root
    cause, deliberately paraphrased to share minimal vocabulary with its
    siblings, per ADR 0003's hard-positive fixture.
  - Distractors: Reusable Tickets that are not a root-cause match for any
    cluster. Three kinds: "topic-adjacent" (near a cluster's topic but easily
    distinguished), "near-miss" (reuses a cluster's vocabulary but describes a
    different root cause, per ADR 0003's hard-negative fixture), and
    "pure-noise" (unrelated to any cluster). A fourth kind,
    "cross-cluster-confusable", could plausibly be mistaken for either of two
    real clusters but matches neither.
  - Demo queries: unresolved tickets (no resolution) for live selection in the
    demo UI -- some that should match a cluster, some that shouldn't.

Writes data/seed_manifest.json recording ground truth (which ticket belongs to
which cluster, and each ticket's variant/kind) since Helpdesk itself has no
concept of a Duplicate Cluster -- that's a domain concept of this project's
eval harness, not Helpdesk's model.
"""

import json
from pathlib import Path

import requests

BASE_URL = "http://helpdesk.localhost:8000"
ADMIN_USER = "Administrator"
ADMIN_PASS = "admin"
RAISED_BY = "requester@example.com"

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_manifest.json"

CLUSTERS = [
    {
        "id": "vpn-crash-after-update",
        "members": [
            {
                "subject": "VPN disconnects immediately after macOS update",
                "description": "Ever since I updated to the latest macOS release, GlobalProtect VPN connects for a few seconds then drops with a 'network unreachable' error.",
                "resolution": "Known incompatibility with the VPN client version bundled before the OS update. Had the user uninstall GlobalProtect completely, reboot, then reinstall version 6.2.1 from the IT portal. Connection has been stable since.",
            },
            {
                "subject": "Cisco AnyConnect won't stay connected since Windows update",
                "description": "After installing the latest Windows security patch, AnyConnect connects briefly then disconnects with error 'the VPN client agent stopped unexpectedly'. Happens every time.",
                "resolution": "The Windows update replaced a network driver AnyConnect depends on. Fix was to uninstall AnyConnect, run the Cisco driver cleanup utility, reboot, then reinstall the client fresh.",
            },
            {
                "subject": "Remote access VPN keeps dropping after laptop update",
                "description": "My VPN client used to work fine, but after last week's forced software update it disconnects randomly, sometimes within a minute of connecting.",
                "resolution": "Root cause was a stale VPN adapter left behind by the update. Removed the orphaned network adapter from Device Manager and reinstalled the VPN client, which resolved the disconnects.",
            },
            {
                "subject": "Can't stay connected to company VPN after patching",
                "description": "Since IT pushed the latest patch to my machine, the VPN either fails to connect at all or drops within seconds. Getting a generic connection error.",
                "resolution": "Confirmed this was caused by the patch resetting network adapter settings. Reset the network stack (netsh winsock reset) and reinstalled the VPN client; connection has been stable since.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Remote office connection tool won't stay up, drops within moments of opening",
                "description": "The client I use to reach internal systems from home has been cutting out constantly since my machine got refreshed with the latest software last week. It connects for a moment then dies.",
                "resolution": "Same as recent similar cases -- the refresh left behind an old network component conflicting with the client. Removed the leftover adapter and did a clean reinstall of the connection client; stable since.",
            },
        ],
    },
    {
        "id": "shared-drive-access-denied-after-reset",
        "members": [
            {
                "subject": "Can't access shared drive after password reset",
                "description": "I reset my password yesterday and now I get 'Access Denied' when trying to open the Finance shared drive, even though I could access it before.",
                "resolution": "Cached credentials on the machine were still pointing to the old password. Cleared saved credentials in Credential Manager and remapped the drive; access restored immediately.",
            },
            {
                "subject": "Permission denied on team folder since changing password",
                "description": "After my password reset this morning, I can no longer open our team's shared network folder — it says I don't have permission.",
                "resolution": "The mapped drive was still authenticating with the old cached password. Disconnected and remapped the network drive with the new credentials, which fixed it.",
            },
            {
                "subject": "Lost access to network share after resetting login",
                "description": "My IT-forced password reset seems to have broken my access to the shared drive I use daily. Getting an access denied popup.",
                "resolution": "This is a known side effect of password resets — Windows keeps stale cached creds for network shares. Ran 'net use * /delete' to clear cached connections, then remapped the drive.",
            },
            {
                "subject": "Shared folder access broken after password change",
                "description": "Right after updating my password through the portal, I started getting access denied errors on the shared drive I need for my daily reports.",
                "resolution": "Stale cached network credentials were the cause. Cleared them via Credential Manager under Windows Credentials, then reconnected to the share — access came back right away.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Team folder won't open, keeps rejecting me",
                "description": "I can't get into our department's network folder anymore -- it just refuses me every time, ever since I had to update my login credentials this week.",
                "resolution": "Old sign-in details were still cached on the machine from before the credential change. Cleared the stored logins and reconnected to the folder, which let them back in.",
            },
        ],
    },
    {
        "id": "calendar-sync-not-updating",
        "members": [
            {
                "subject": "Calendar not syncing between phone and laptop",
                "description": "Meetings I accept on my laptop aren't showing up on my phone's calendar app, and it's been like this for two days.",
                "resolution": "The mobile device's account sync had silently failed. Removed and re-added the work account on the phone, forcing a full resync, which brought everything back in line.",
            },
            {
                "subject": "Outlook calendar not updating with new meeting invites",
                "description": "New meeting invites people send me aren't appearing in my Outlook calendar even though I get the email notification.",
                "resolution": "Outlook's local cache had become corrupted. Ran Outlook in /cleanreminders and /resetfoldernames mode, then let it fully resync from the server, which fixed the missing events.",
            },
            {
                "subject": "My calendar on my phone is out of date",
                "description": "I updated my availability on desktop yesterday but my phone still shows the old schedule — coworkers are booking slots I've already blocked off.",
                "resolution": "Diagnosed a broken sync token on the mobile calendar profile. Removed the calendar account and re-added it from scratch, which reset the sync token and resolved the mismatch.",
            },
            {
                "subject": "Meetings accepted on one device don't show on another",
                "description": "If I accept a meeting on my laptop, it doesn't reflect on my tablet's calendar for hours, sometimes not at all.",
                "resolution": "Traced it to a stalled background sync process on the tablet. Forced a manual account resync in settings and confirmed events appeared correctly afterward.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Schedule on my mobile is behind what's on my desktop",
                "description": "Appointments I set up at my desk yesterday still aren't showing up when I check my phone -- it's like the two aren't talking to each other anymore.",
                "resolution": "The device's connection to the account had quietly broken. Deleted and re-added the account on the phone to force a fresh handshake; entries appeared correctly after.",
            },
        ],
    },
    {
        "id": "printer-jobs-stuck-after-update",
        "members": [
            {
                "subject": "Print jobs stuck in queue since Windows update",
                "description": "Every document I try to print just sits in the print queue as 'Printing' and never actually prints, ever since the latest Windows update.",
                "resolution": "The Windows update corrupted the print spooler service. Stopped the Print Spooler service, cleared the spooler folder contents, and restarted the service — printing resumed normally.",
            },
            {
                "subject": "Printer not printing, jobs pile up in queue",
                "description": "Nothing I send to the office printer is coming out. The queue just keeps growing with pending jobs after the recent OS patch.",
                "resolution": "Found the print spooler stuck after the patch. Cleared the spool folder contents and restarted the Print Spooler service, which cleared the backlog.",
            },
            {
                "subject": "Documents won't print, stuck in printing status",
                "description": "Since IT rolled out the update this week, my print jobs get stuck showing 'Printing' forever and nothing comes out of the printer.",
                "resolution": "Same root cause as other recent tickets — the update left the spooler in a bad state. Restarted the spooler service after clearing its cache folder, resolving the stuck queue.",
            },
            {
                "subject": "Print queue frozen after latest patch",
                "description": "The print queue on my machine has been frozen since the update went out — jobs just accumulate and nothing prints.",
                "resolution": "Print spooler service had hung due to the update. Killed and restarted the spooler after purging the spool folder; print jobs now process normally.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Nothing comes out of the office printer anymore",
                "description": "Every document I send just piles up waiting and never actually prints -- started right after my machine got its latest round of patches installed.",
                "resolution": "The patch left the background print service in a broken state, same pattern as other recent cases. Restarted the service after clearing its temporary job folder; output resumed immediately.",
            },
        ],
    },
    {
        "id": "sso-login-redirect-loop",
        "members": [
            {
                "subject": "Stuck in login loop trying to access company portal",
                "description": "Every time I try to log into the internal portal, it redirects me back to the SSO login page over and over without ever letting me in.",
                "resolution": "The user's browser had a stale SSO session cookie conflicting with the identity provider. Cleared cookies for the SSO domain and had them log in fresh, which broke the loop.",
            },
            {
                "subject": "SSO keeps bouncing me back to login page",
                "description": "I enter my credentials, it looks like it's loading, then it just sends me back to the login screen again. This repeats endlessly.",
                "resolution": "Diagnosed a clock skew issue between the user's machine and the identity provider causing token validation to fail. Corrected the system time and the redirect loop stopped.",
            },
            {
                "subject": "Can't get past company login screen, redirect loop",
                "description": "Trying to sign into any internal tool just cycles me back to the login page infinitely, never actually authenticating.",
                "resolution": "Found conflicting SSO sessions from two different browser profiles. Signed out of all sessions and cleared local storage for the identity provider domain, which resolved it.",
            },
            {
                "subject": "Login page keeps refreshing instead of letting me in",
                "description": "After entering my password, the SSO page just reloads itself repeatedly instead of taking me to the app I'm trying to reach.",
                "resolution": "Root cause was an expired SAML assertion cache on the client side. Cleared the browser's site data for the SSO provider and retried, which let the login complete normally.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Portal won't let me in, just keeps bouncing back to sign-in",
                "description": "Trying to reach any internal tool sends me right back to the sign-in screen in an endless cycle, never actually getting me through.",
                "resolution": "Same underlying issue as recent cases -- a leftover authentication session was conflicting with the identity provider. Signed the user out everywhere and cleared local site data; loop stopped.",
            },
        ],
    },
    {
        "id": "slack-notifications-not-showing",
        "members": [
            {
                "subject": "Not getting Slack notifications on desktop",
                "description": "I'm not seeing any desktop notification pop-ups for new Slack messages even though notifications are enabled in settings.",
                "resolution": "macOS system notification permissions for Slack had been silently revoked. Re-enabled notifications for the Slack app under System Settings > Notifications, which fixed it.",
            },
            {
                "subject": "Slack desktop app silent, no alerts for new messages",
                "description": "New messages come in but I never get a sound or banner notification on my desktop, only when I open the app manually.",
                "resolution": "Slack's own in-app notification preference had reset to 'nothing' after an update. Reset the preference to 'all new messages' in Slack's notification settings, resolving the issue.",
            },
            {
                "subject": "Missing message alerts in Slack desktop client",
                "description": "Coworkers say they've messaged me but I never see a notification banner or badge count update on the desktop app.",
                "resolution": "Found Do Not Disturb had been auto-scheduled and never turned off. Disabled the scheduled DND window in Slack preferences, restoring normal notification behavior.",
            },
            {
                "subject": "Slack won't alert me to new DMs",
                "description": "Direct messages aren't triggering any notification on my desktop client, I only notice them if I happen to check the app.",
                "resolution": "Diagnosed notification permissions being blocked at the OS level after a recent update. Reset app notification permissions and confirmed alerts started working again.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Team chat app staying completely quiet on new messages",
                "description": "I'm not being alerted at all when people message me in our team chat tool -- no sound, no popup, nothing, unless I happen to have the window open.",
                "resolution": "Consistent with other recent reports -- the app's permission to alert had been quietly turned off at the system level. Re-enabled it in system settings; alerts resumed.",
            },
        ],
    },
    {
        "id": "laptop-wont-wake-from-sleep",
        "members": [
            {
                "subject": "Laptop frozen when opening lid after sleep",
                "description": "Whenever I open my laptop after it's been sleeping, the screen stays black or frozen and I have to hard reset it.",
                "resolution": "Identified an outdated graphics driver causing the wake failure. Updated the GPU driver to the latest version, which resolved the freeze-on-wake issue.",
            },
            {
                "subject": "Computer won't wake up from sleep mode",
                "description": "My laptop just won't respond when I try to wake it from sleep — screen stays dark and only a hard power-off works.",
                "resolution": "Found a faulty USB peripheral was preventing proper wake signaling. Unplugged the dock and updated its firmware, which fixed the wake-from-sleep problem.",
            },
            {
                "subject": "Screen stays black after closing and reopening laptop lid",
                "description": "After the laptop sleeps and I reopen the lid, the screen just stays black — no response until I force a restart.",
                "resolution": "Traced to a corrupted power plan setting. Reset the power plan to the default balanced profile and reinstalled the display driver, which resolved the black screen on wake.",
            },
            {
                "subject": "Laptop unresponsive after being asleep",
                "description": "The machine becomes completely unresponsive after sitting idle and going to sleep — I have to force restart every time.",
                "resolution": "Determined a background process was hanging the wake sequence. Disabled fast startup and updated the chipset drivers, which fixed the unresponsiveness on wake.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Machine unresponsive every time I open it back up",
                "description": "After it's been sitting idle for a while, opening the lid does nothing -- display stays dark and I end up having to force a restart to get anything back.",
                "resolution": "Matches the pattern of other recent cases -- an out-of-date display driver was interfering with wake. Updated it to the current version; problem hasn't recurred.",
            },
        ],
    },
    {
        "id": "password-reset-email-not-received",
        "members": [
            {
                "subject": "Never received password reset email",
                "description": "I requested a password reset link over an hour ago and still haven't gotten the email, checked spam too.",
                "resolution": "Found the reset email was stuck in the outbound mail queue due to a temporary relay issue. Manually triggered a resend after the queue cleared, and the user received it within minutes.",
            },
            {
                "subject": "Password reset link never arrived in my inbox",
                "description": "I tried resetting my password twice now and neither email has shown up, not even in junk mail.",
                "resolution": "The user's mailbox had a filter silently discarding system emails. Added an allow-list rule for the IT domain and manually resent the reset email, which then arrived normally.",
            },
            {
                "subject": "Reset password email missing",
                "description": "Requested a new password via the 'forgot password' link but no email has come through after 30+ minutes.",
                "resolution": "Diagnosed a delay in the transactional email service; emails were being sent but queued for over an hour. Escalated to the email provider and manually triggered delivery for this user.",
            },
            {
                "subject": "No email showing up for my password reset request",
                "description": "I've requested the reset email multiple times and nothing arrives, not in inbox or spam folder.",
                "resolution": "User's email address had a typo in the account profile from a prior update. Corrected the email address on file and resent the reset link successfully.",
            },
            {
                "variant": "low-lexical-overlap",
                "subject": "Requested a new login link but nothing ever shows up",
                "description": "I asked for a way to get back into my account almost two hours ago and still have nothing in my inbox, checked everywhere including junk.",
                "resolution": "Consistent with recent similar cases -- delivery had stalled somewhere in the outbound pipeline. Manually pushed the message through after confirming the queue had cleared; arrived shortly after.",
            },
        ],
    },
]

DISTRACTORS = [
    # Topically adjacent to a cluster, but not a root-cause match
    {
        "adjacent_to": "vpn-crash-after-update",
        "subject": "VPN connects fine but internet is extremely slow",
        "description": "VPN connects without issue but browsing and downloads are painfully slow, unrelated to disconnects.",
        "resolution": "Identified split-tunneling was disabled, routing all traffic through VPN unnecessarily. Enabled split-tunneling for non-corporate traffic, restoring normal speeds.",
    },
    {
        "adjacent_to": "shared-drive-access-denied-after-reset",
        "subject": "Requesting access to new shared drive for marketing team",
        "description": "I've joined the marketing team and need access granted to their shared drive, I've never had access before.",
        "resolution": "Granted appropriate read/write permissions on the marketing shared drive via the access management portal.",
    },
    {
        "adjacent_to": "calendar-sync-not-updating",
        "subject": "Meeting invites showing wrong timezone",
        "description": "Meetings scheduled by colleagues in another office show up in the wrong timezone on my calendar.",
        "resolution": "User's calendar timezone setting was manually overridden. Reset the timezone setting to auto-detect based on system locale.",
    },
    {
        "adjacent_to": "printer-jobs-stuck-after-update",
        "subject": "New printer not showing up in the printer list",
        "description": "IT just installed a new printer on our floor but I can't find it when trying to add a printer on my laptop.",
        "resolution": "The printer hadn't been shared on the print server yet. Added the printer share and pushed the driver via group policy.",
    },
    {
        "adjacent_to": "sso-login-redirect-loop",
        "subject": "MFA push notification never arrives on my phone",
        "description": "When logging in, I approve via the Okta app, but the push notification for MFA verification never shows up.",
        "resolution": "Found the Okta Verify app had lost its device registration after a phone OS update. Re-enrolled the device in Okta Verify, restoring push notifications.",
    },
    {
        "adjacent_to": "slack-notifications-not-showing",
        "subject": "Slack messages are sending twice",
        "description": "Every message I send in Slack appears duplicated in the channel, both to me and other members.",
        "resolution": "Identified a misbehaving browser extension intercepting and resending form submissions. Disabled the extension, which stopped the duplicate sends.",
    },
    {
        "adjacent_to": "laptop-wont-wake-from-sleep",
        "subject": "Laptop battery drains overnight even when powered off",
        "description": "I shut my laptop down completely each night but the battery is significantly drained by morning.",
        "resolution": "Found a BIOS setting for 'wake on LAN' was keeping components partially powered. Disabled wake-on-LAN in BIOS, which stopped the overnight drain.",
    },
    {
        "adjacent_to": "password-reset-email-not-received",
        "subject": "Account locked out after failed login attempts",
        "description": "I mistyped my password a few times and now my account says it's locked and won't let me try again.",
        "resolution": "Confirmed the lockout was from the standard 5-attempt policy. Manually unlocked the account in Active Directory and reset the failed-attempt counter.",
    },
    # Pure noise, not adjacent to any cluster
    {
        "adjacent_to": None,
        "subject": "Monitor flickering when connected via HDMI",
        "description": "External monitor flickers intermittently when connected to my laptop via HDMI cable.",
        "resolution": "Faulty HDMI cable was the cause. Replaced with a new certified cable, which resolved the flickering.",
    },
    {
        "adjacent_to": None,
        "subject": "Zoom camera not detected during meetings",
        "description": "My webcam shows as unavailable in Zoom even though it works fine in other apps.",
        "resolution": "Zoom's camera permission had been revoked at the OS level after a recent update. Re-granted camera access to Zoom in system privacy settings.",
    },
    {
        "adjacent_to": None,
        "subject": "Software license expired warning appears incorrectly",
        "description": "I keep getting a 'license expired' popup for our design software even though our subscription is active.",
        "resolution": "The local license cache was stale. Forced a manual license re-validation from the software's account menu, clearing the false warning.",
    },
    {
        "adjacent_to": None,
        "subject": "Keyboard shortcuts stopped working in my IDE",
        "description": "Common keyboard shortcuts in my code editor suddenly stopped responding after an extension update.",
        "resolution": "A newly installed extension was capturing the same keybindings. Disabled the conflicting extension and shortcuts resumed working.",
    },
    {
        "adjacent_to": None,
        "subject": "File upload fails for files over 25MB",
        "description": "Trying to upload attachments larger than 25MB to the internal ticketing tool fails silently every time.",
        "resolution": "The upload size limit was set below the intended threshold. Raised the max upload size setting in the ticketing tool's admin config.",
    },
    {
        "adjacent_to": None,
        "subject": "Company intranet page returns 500 error",
        "description": "The intranet homepage has been showing a server error for the past hour, other pages seem fine.",
        "resolution": "Identified a crashed backend service behind the intranet homepage widget. Restarted the affected service, restoring the page.",
    },
    {
        "adjacent_to": None,
        "subject": "Time tracking app not syncing hours to payroll",
        "description": "Hours I log in the time tracking app aren't showing up when payroll pulls the report.",
        "resolution": "Found the integration sync job had silently failed two days prior. Manually re-ran the sync job and confirmed hours appeared correctly in payroll.",
    },
    {
        "adjacent_to": None,
        "subject": "Conference room booking system double-books slots",
        "description": "Our room booking tool is letting two different meetings book the same room at the same time.",
        "resolution": "Identified a race condition in the booking tool when two requests hit simultaneously. Reported to the vendor and applied their patch, which fixed the double-booking.",
    },
    # Near-miss: reuses a cluster's vocabulary but has a different root cause
    # (per ADR 0003). Should rank close to its cluster on lexical/semantic
    # similarity but fail Root-Cause Similarity, so the reranker + Match
    # Threshold -- not fusion ranking -- is what should keep it out.
    {
        "kind": "near-miss",
        "adjacent_to": "vpn-crash-after-update",
        "subject": "VPN takes several minutes to connect after the latest update",
        "description": "Since the recent forced update, the VPN client takes 5+ minutes just to establish a connection, though once connected it seems to work fine.",
        "resolution": "The update reset the VPN client's DNS resolution order, causing lengthy handshake retries. Corrected the DNS server order in the VPN client's network settings, connection time back to normal.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "shared-drive-access-denied-after-reset",
        "subject": "Access denied on shared drive after password reset, remapping doesn't help",
        "description": "Reset my password like requested and now get 'Access Denied' on the Finance shared drive -- I already tried remapping it and clearing cached credentials but nothing works.",
        "resolution": "This one wasn't cached credentials -- the password reset process had accidentally dropped the user from the Finance security group. Re-added their account to the Finance AD group, access restored.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "calendar-sync-not-updating",
        "subject": "Calendar showing duplicate meetings after recent sync issue",
        "description": "My calendar has started showing every meeting twice since yesterday -- looks similar to the sync problems others have reported, but I'm getting doubles, not missing events.",
        "resolution": "Different cause than typical sync lag -- the user had two calendar profiles linked to the same account. Removed the duplicate profile, doubled entries stopped appearing.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "printer-jobs-stuck-after-update",
        "subject": "Print jobs stuck in queue since Windows update, restarting spooler didn't help",
        "description": "Same as the other post-update printing tickets -- jobs pile up in the queue -- but I already tried restarting the print spooler and clearing the folder and it's still stuck.",
        "resolution": "This one wasn't the spooler -- the update had silently rolled back to a generic driver incompatible with the printer model. Reinstalled the correct manufacturer driver, jobs started processing again.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "sso-login-redirect-loop",
        "subject": "Stuck in login loop after entering credentials, same as recent SSO issues",
        "description": "Getting bounced back to the SSO login page repeatedly just like other reported cases -- already cleared my cookies and tried a different browser, still looping.",
        "resolution": "Not a session/cache issue this time -- the account had been disabled during an offboarding cleanup by mistake. Re-enabled the account in the identity provider, login completed normally on first try.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "slack-notifications-not-showing",
        "subject": "Not getting Slack notifications, already checked permissions and settings",
        "description": "Same complaint as other recent tickets -- no alerts for new Slack messages -- but I already confirmed OS notification permissions are enabled and DND is off, still nothing.",
        "resolution": "Turned out the desktop app was signed into the wrong Slack workspace after a recent re-login, so messages in the main workspace never triggered anything locally. Switched to the correct workspace, notifications working again.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "laptop-wont-wake-from-sleep",
        "subject": "Laptop won't wake from sleep, already updated drivers and reset power plan",
        "description": "Same symptoms as other recent tickets -- screen stays black after sleep -- but I've already updated the graphics driver and reset the power plan like those other fixes, no change.",
        "resolution": "Different root cause here -- battery diagnostics showed the internal battery was failing and couldn't supply enough power to resume from low-power state reliably. Replaced the battery, wake issue resolved.",
    },
    {
        "kind": "near-miss",
        "adjacent_to": "password-reset-email-not-received",
        "subject": "Password reset email still not received, already tried resending twice",
        "description": "Same issue as other recent tickets -- no reset email arriving -- but I've already had it resent twice and checked spam like those fixes suggested, still nothing.",
        "resolution": "Different cause than the delivery issues in other tickets -- the user was requesting resets using an old personal email no longer on file, instead of their current work address. Had them request it via the profile's current work email, arrived immediately.",
    },
    # Cross-cluster confusables: could plausibly be mistaken for either of two
    # real clusters, but match neither (per ADR 0003).
    {
        "kind": "cross-cluster-confusable",
        "adjacent_to": None,
        "subject": "Can't sign into company portal, keep getting bounced around",
        "description": "Every time I try to log into the internal portal I either get an error page or get sent back to where I started -- not sure if it's my password or something else, this has been going on for a day.",
        "resolution": "Traced to an expired SAML certificate on the identity provider's side for this specific application integration, unrelated to the user's browser or account. Renewed the certificate on the IdP configuration, portal access restored for all affected users.",
    },
    {
        "kind": "cross-cluster-confusable",
        "adjacent_to": None,
        "subject": "App badge counts and previews are stale, not refreshing live",
        "description": "The little unread counts and message previews on my dock icons aren't updating unless I manually reopen the apps -- affects a couple of different apps, feels similar to other notification or sync complaints people have filed.",
        "resolution": "This was a system-wide Notification Center cache issue after a recent macOS update, not specific to any one app. Rebuilt the Notification Center database via Terminal and restarted; badge counts and previews began refreshing normally across all apps.",
    },
    {
        "kind": "cross-cluster-confusable",
        "adjacent_to": None,
        "subject": "Can't reach any internal resources over VPN after password change",
        "description": "Since resetting my password this week, my VPN won't connect to the office network at all -- not sure if it's a VPN client issue like others have had or something with my new password.",
        "resolution": "Neither the client software nor local cached credentials were the issue -- the RADIUS server hadn't yet synced the new password from the identity provider, causing VPN authentication to fail for a short window. Waited for directory sync to complete and had the user retry; connected successfully without any client changes.",
    },
]

DEMO_QUERIES = [
    {
        "expected_cluster": "vpn-crash-after-update",
        "subject": "VPN drops constantly since this week's update",
        "description": "Since the mandatory software update rolled out this week, my VPN connection keeps dropping every couple of minutes.",
    },
    {
        "expected_cluster": "printer-jobs-stuck-after-update",
        "subject": "Nothing prints, jobs stuck in queue after update",
        "description": "After today's Windows update, everything I send to print just sits in the queue and never comes out.",
    },
    {
        "expected_cluster": "password-reset-email-not-received",
        "subject": "Haven't received my password reset email",
        "description": "I requested a password reset almost an hour ago and the email still hasn't shown up anywhere.",
    },
    {
        "expected_cluster": None,
        "subject": "Need approval to expense a new monitor purchase",
        "description": "I bought a second monitor for home office use and need it approved for reimbursement through expenses.",
    },
    {
        "expected_cluster": None,
        "subject": "Requesting badge access to the server room for new hire",
        "description": "We have a new infrastructure hire starting Monday who needs physical badge access to the server room.",
    },
]


def login(session: requests.Session) -> None:
    resp = session.post(
        f"{BASE_URL}/api/method/login",
        data={"usr": ADMIN_USER, "pwd": ADMIN_PASS},
    )
    resp.raise_for_status()


def create_ticket(session: requests.Session, subject: str, description: str) -> str:
    resp = session.post(
        f"{BASE_URL}/api/resource/HD Ticket",
        json={
            "subject": subject,
            "description": f"<p>{description}</p>",
            "raised_by": RAISED_BY,
            "priority": "Medium",
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]["name"]


def resolve_ticket(session: requests.Session, name: str, resolution: str) -> None:
    resp = session.put(
        f"{BASE_URL}/api/resource/HD Ticket/{name}",
        json={"resolution_details": f"<p>{resolution}</p>", "status": "Resolved"},
    )
    resp.raise_for_status()


def main() -> None:
    session = requests.Session()
    login(session)

    manifest = {"clusters": [], "distractors": [], "demo_queries": []}

    for cluster in CLUSTERS:
        members = []
        for member in cluster["members"]:
            variant = member.get("variant", "standard")
            name = create_ticket(session, member["subject"], member["description"])
            resolve_ticket(session, name, member["resolution"])
            members.append({"ticket_name": name, "variant": variant})
            print(f"[cluster:{cluster['id']}:{variant}] {name} - {member['subject']}")
        manifest["clusters"].append({"id": cluster["id"], "members": members})

    for distractor in DISTRACTORS:
        kind = distractor.get("kind") or (
            "topic-adjacent" if distractor["adjacent_to"] else "pure-noise"
        )
        name = create_ticket(session, distractor["subject"], distractor["description"])
        resolve_ticket(session, name, distractor["resolution"])
        print(f"[distractor:{kind}] {name} - {distractor['subject']}")
        manifest["distractors"].append(
            {"ticket_name": name, "adjacent_to": distractor["adjacent_to"], "kind": kind}
        )

    for query in DEMO_QUERIES:
        name = create_ticket(session, query["subject"], query["description"])
        print(f"[demo-query] {name} - {query['subject']}")
        manifest["demo_queries"].append(
            {"ticket_name": name, "expected_cluster": query["expected_cluster"]}
        )

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote manifest to {MANIFEST_PATH}")
    total_cluster_tickets = sum(len(c["members"]) for c in CLUSTERS)
    total = total_cluster_tickets + len(DISTRACTORS) + len(DEMO_QUERIES)
    print(f"Seeded {total} tickets total.")


if __name__ == "__main__":
    main()
