#!/usr/bin/python
"""Flask app to run data retrieval tasks for Open Humans"""

import os

from celery import Celery
import flask
from flask import request

from data_retrieval.american_gut import create_amgut_ohdataset
from data_retrieval.twenty_three_and_me import create_23andme_ohdataset

PORT = 5000
STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'files')
S3_BUCKET_NAME = 'oh-data-export-testing-20141020'


#####################################################################
# Set up celery and tasks.
def make_celery(app):
    """Set up celery tasks for an app."""
    celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery

ohdata_app = flask.Flask("client")
ohdata_app.config.update(
    CELERY_BROKER_URL='amqp://',
)
celery_worker = make_celery(ohdata_app)

from celery.signals import after_task_publish


@after_task_publish.connect
def task_sent_handler(sender=None, body=None, **kwargs):
    print('after_task_publish for task id {body[id]}'.format(
        body=body,
    ))


@celery_worker.task()
def start_amgut_ohdataset(barcode, s3_key_name):
    """Task to initiate retrieval of American Gut data set"""
    create_amgut_ohdataset(barcode=barcode,
                           s3_bucket_name=S3_BUCKET_NAME,
                           s3_key_name=s3_key_name,)


@celery_worker.task()
def start_23andme_ohdataset(access_token, profile_id, s3_key_name):
    """Task to initiate retrieval of 23andme data set"""
    create_23andme_ohdataset(access_token=access_token,
                             profile_id=profile_id,
                             s3_bucket_name=S3_BUCKET_NAME,
                             s3_key_name=s3_key_name)


#####################################################################
# Pages to receive task requests.
@ohdata_app.route('/23andme', methods=['GET', 'POST'])
def twenty_three_and_me():
    """Page to receive 23andme task request"""
    # if request.method == 'POST':
    start_23andme_ohdataset.delay(access_token=request.args['access_token'],
                                  profile_id=request.args['profile_id'],
                                  s3_key_name=request.args['s3_key_name'])
    return "23andme dataset started"


@ohdata_app.route('/amgut', methods=['GET', 'POST'])
def american_gut():
    """Page to receive American Gut task request"""
    # if request.method == 'POST':
    start_amgut_ohdataset.delay(barcode=request.args['barcode'],
                                s3_key_name=request.args['s3_key_name'])
    return "Amgut dataset started"


if __name__ == '__main__':
    print "A local client for Open Humans data extraction is now initialized."
    ohdata_app.run(debug=True, port=PORT)
