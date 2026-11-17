import subprocess
import json
import time
import argparse
import csv
import os
from datetime import datetime

def get_location():
    try:
        result = subprocess.run(
            ["termux-location", "-p", "gps", "-r", "once"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            print("GPS error:", result.stderr)
            return None

        data = json.loads(result.stdout)

        return {
            "timestamp": datetime.now().isoformat(),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "accuracy": data.get("accuracy")
        }

    except Exception as error:
        print("Error:", error)
        return None


def save_location(data):
    if not data:
        return

    filename = "location_history.csv"
    file_exists = os.path.exists(filename)

    with open(filename, "a", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "latitude",
                "longitude",
                "accuracy"
            ]
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(data)


def track(interval):
    print("Phone Location Tracker Started")
    print("Tracking this device with permission.")
    print("Press CTRL+C to stop.\n")

    try:
        while True:
            location = get_location()

            if location:
                save_location(location)

                print("Time:", location["timestamp"])
                print("Latitude:", location["latitude"])
                print("Longitude:", location["longitude"])
                print("Accuracy:", location["accuracy"])

                lat = location["latitude"]
                lon = location["longitude"]

                if lat is not None and lon is not None:
                    print(
                        f"Map: https://www.google.com/maps?q={lat},{lon}"
                    )

                print("-" * 40)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nTracker stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Educational GPS tracker for your own phone"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="GPS update interval in seconds"
    )

    args = parser.parse_args()

    if args.interval < 10:
        parser.error("Interval must be at least 10 seconds")

    track(args.interval)
