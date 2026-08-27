"""Subscribe to measurements, store them in PostgreSQL, and expose an API."""

import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg2
from flask import Flask, jsonify, request


# Configuration is loaded once at process start.
DATABASE_URL = os.environ["DATABASE_URL"]
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "meter/data")
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
MQTT_CA_CERT = os.environ.get("MQTT_CA_CERT", "/app/ca.crt")
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))
AUTH_PASSWORD_HASH = os.environ["AUTH_PASSWORD_HASH"]
AUTH_SEED = os.environ["AUTH_SEED"]

app = Flask(__name__)
database_lock = threading.Lock()


def database_connection():
	return psycopg2.connect(DATABASE_URL)


def initialize_database():
	with database_connection() as connection, connection.cursor() as cursor:
		cursor.execute("""
			CREATE TABLE IF NOT EXISTS measurements (
				timestamp TIMESTAMPTZ NOT NULL,
				temperature REAL NOT NULL,
				humidity REAL NOT NULL
			)
		""")
		cursor.execute(
			"CREATE INDEX IF NOT EXISTS measurements_timestamp_idx "
			"ON measurements (timestamp DESC)"
		)


def save_measurement(timestamp, temperature, humidity):
	with database_lock, database_connection() as connection, connection.cursor() as cursor:
		insert_query = (
			"INSERT INTO measurements (timestamp, temperature, humidity) "
			"VALUES (%(timestamp)s, %(temperature)s, %(humidity)s)"
		)
		insert_parameters = {
			"timestamp": timestamp,
			"temperature": temperature,
			"humidity": humidity,
		}
		cursor.execute(insert_query, insert_parameters)


def mqtt_message(_client, _userdata, message):
	try:
		payload = json.loads(message.payload.decode("utf-8"))
		timestamp = payload.get("Timestamp", datetime.now(timezone.utc))
		if isinstance(timestamp, str):
			timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
		save_measurement(timestamp, float(payload["Temperature"]), float(payload["Humidity"]))
	except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
		app.logger.warning("Ignoring invalid measurement: %s", error)


def is_authenticated():
	token = request.headers.get("Authorization", "")
	if token.lower().startswith("bearer "):
		token = token[7:]
	# AUTH_PASSWORD_HASH is SHA-256(password + AUTH_SEED), encoded as hex.
	actual = hashlib.sha256((token + AUTH_SEED).encode("utf-8")).hexdigest()
	authenticated = hmac.compare_digest(actual, AUTH_PASSWORD_HASH)
	if not authenticated:
		app.logger.warning("Authentication failed for /measurements")
	return authenticated


def parse_query_datetime(value):
	parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
	return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


@app.get("/measurements")
def get_measurements():
	if not is_authenticated():
		return jsonify(error="authentication required"), 401
	start = request.args.get("start")
	end = request.args.get("end")
	try:
		start_datetime = parse_query_datetime(start) if start else None
		end_datetime = parse_query_datetime(end) if end else None
		if start_datetime and end_datetime and start_datetime > end_datetime:
			raise ValueError
	except ValueError:
		return jsonify(error="start and end must be valid ISO-8601 timestamps, with start before end"), 400

	with database_lock, database_connection() as connection, connection.cursor() as cursor:
		cursor.execute(
			"SELECT timestamp, temperature, humidity FROM measurements"
			" WHERE (%s::timestamptz IS NULL OR timestamp >= %s::timestamptz)"
			" AND (%s::timestamptz IS NULL OR timestamp <= %s::timestamptz)"
			" ORDER BY timestamp DESC",
			(start_datetime, start_datetime, end_datetime, end_datetime),
		)
		rows = cursor.fetchall()
	return jsonify([
		{"Timestamp": timestamp.isoformat(), "Temperature": temperature, "Humidity": humidity}
		for timestamp, temperature, humidity in rows
	])


def mqtt_loop():
	client = mqtt.Client()
	client.on_message = mqtt_message
	client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
	client.tls_set(ca_certs=MQTT_CA_CERT)
	client.connect(MQTT_HOST, MQTT_PORT)
	client.subscribe(MQTT_TOPIC)
	client.loop_forever()


if __name__ == "__main__":
	initialize_database()
	threading.Thread(target=mqtt_loop, daemon=True).start()
	app.run(host=WEB_HOST, port=WEB_PORT)
