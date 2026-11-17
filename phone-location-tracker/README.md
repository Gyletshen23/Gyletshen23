# Phone Location Tracker

An educational script that logs the GPS location of **your own** Android phone
using [Termux](https://termux.dev) and the Termux:API add-on.

## Setup (on the phone)

1. Install **Termux** and **Termux:API** (both from F-Droid).
2. In Termux:
   ```sh
   pkg install python termux-api
   termux-setup-storage   # optional
   ```
3. Grant Termux:API the location permission in Android settings.

## Usage

```sh
python tracker.py               # one reading every 60 seconds
python tracker.py --interval 30 # every 30 seconds (minimum 10)
```

Each reading is printed with a Google Maps link and appended to
`location_history.csv` (timestamp, latitude, longitude, accuracy) in the
current directory. Press `Ctrl+C` to stop.

`location_history.csv` is git-ignored so your location data is never committed.

Only use this on a device you own, or with the clear consent of its owner.
