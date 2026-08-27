#!/usr/bin/env python3
"""Publish one message to a password-protected MQTT broker."""

import argparse
import os
import datetime
import time
from xxlimited import Str

import paho.mqtt.client as mqtt


def measure () -> Str:
    """Simulate a measurement and return it as a JSON string."""
    import json
    import random

    temperature = round(random.uniform(20.0, 30.0), 1)
    humidity = round(random.uniform(30.0, 60.0), 1)
    data = {"Timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "Temperature": str(temperature), "Humidity": str(humidity)}
    return json.dumps(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", help="MQTT topic", default=os.getenv("MQTT_TOPIC", "meter/data"))
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "8883")))
    parser.add_argument("--ca-cert", default=os.getenv("MQTT_CA_CERT", "/app/ca.crt"))
    parser.add_argument("--username", default=os.getenv("MQTT_USERNAME"))
    parser.add_argument("--password", default=os.getenv("MQTT_PASSWORD"))
    parser.add_argument("-- qos", dest="qos", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--retain", action="store_true")
    args = parser.parse_args()

    if not args.username or args.password is None:
        parser.error("MQTT_USERNAME and MQTT_PASSWORD (or --username and --password) are required")

    client = mqtt.Client()
    client.username_pw_set(args.username, args.password)
    client.tls_set(ca_certs=args.ca_cert)

    try:
        client.connect(args.host, args.port, keepalive=60)

        while True:
            publish_result = client.publish(args.topic, measure(), qos=args.qos, retain=args.retain)
            if hasattr(publish_result, "wait_for_publish"):
                publish_result.wait_for_publish()
                publish_rc = publish_result.rc
            else:
                publish_rc = publish_result[0]
            if publish_rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"publish failed: {mqtt.error_string(publish_rc)}")

            time.sleep(30)  # wait for 30 seconds before publishing the next message
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
