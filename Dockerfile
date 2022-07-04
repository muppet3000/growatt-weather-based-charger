FROM python:3-slim

COPY requirements.txt /tmp/

RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

RUN mkdir /opt/growatt-charger

COPY defaults /opt/growatt-charger/defaults
COPY bin /opt/growatt-charger/bin

VOLUME /opt/growatt-charger/conf
VOLUME /opt/growatt-charger/output
VOLUME /opt/growatt-charger/logs

ENTRYPOINT ["/opt/growatt-charger/bin/growatt-charger.py"]
