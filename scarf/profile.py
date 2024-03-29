import imghdr
import base64
import logging
import pytz 
import datetime

from scarf import app
from core import redirect_back, SiteUser, NoUser, check_email, send_mail
from main import page_not_found, PageData

from flask import redirect, url_for, render_template, session, request, flash, make_response
from string import ascii_letters, digits
import random
import re
 
logger = logging.getLogger(__name__)

@app.route('/forgotpw', methods=['GET', 'POST'])
def userupdate():
    pd = PageData()
    if request.method == 'POST':
        try:
            user = SiteUser.create(request.form['username'])
            user.forgot_pw_reset(request.remote_addr)
        except NoUser:
            email_user = check_email(request.form['email'])
            if email_user:
                email_user.forgot_pw_reset(request.remote_addr)

        flash('A new password has been e-mailed. Please remember to change it when you log in.')
        return redirect(url_for('index'))

    return render_template('forgotpw.html', pd=pd)

#TODO /user/<user>/<whatever>
@app.route('/emailupdate', methods=['GET', 'POST'])
def emailupdate():
    pd = PageData()
    if 'username' in session:
        if request.method == 'POST':
            try:
                user = SiteUser.create(session['username'])
            except NoUser:
                return render_template('error.html', pd=pd)

            try:
                user.authenticate(request.form['password'])
            except AuthFail:
                flash("Please check your current password and try again")
                return redirect('/user/' + user.username)

            email = request.form['email']

            if not re.match("[^@]+@[^@]+\.[^@]+", request.form['email']):
                flash("Invalid email address")
                return redirect('/user/' + user.username)

            user.newemail(email)

            flash("Your email address has been changed.")
            return redirect('/user/' + user.username)

    return redirect(url_for('index'))
 
#TODO /user/<user>/<whatever>
@app.route('/pwreset', methods=['GET', 'POST'])
def pwreset():
    pd = PageData()
    if 'username' in session:
        ret = False
        if request.method == 'POST':
            try:
                user = SiteUser.create(session['username'])
            except NoUser:
                return render_template('error.html', pd=pd)

            try:
                user.authenticate(request.form['password'])
            except AuthFail:
                flash("Please check your current password and try again")
                return redirect('/user/' + user.username)

            pass1 = request.form['newpassword']
            pass2 = request.form['newpassword2']

            if pass1 != pass2:
                flash("The passwords entered don't match.")
                ret = True

            if len(pass1) < 6:
                flash("Your new password is too short, it must be at least 6 characters")
                ret = True

            if ret:
                return redirect('/user/' + user.username)

            user.newpassword(request.form['newpassword'])

            flash("Your password has been reset.")
            return redirect('/user/' + user.username)

    return redirect(url_for('index'))

@app.route('/user/<username>/prefs', methods=['POST'])
def updateprefs(username):
    pd = PageData()
    if 'username' in session:
        ret = False
        if request.method == 'POST':
            try:
                user = SiteUser.create(session['username'])
                profile = user.profile()
            except NoUser:
                return render_template('error.html', pd=pd)

            if request.form['timezone'] in pytz.common_timezones:
                logger.info('timezone updated for for {}'.format(username))
                profile.profile['timezone'] = request.form['timezone']

            profile.profile['summary'] = request.form['summary']

            profile.update()

            flash("Your profile has been updated.")
            logger.info('profile updated for for {}'.format(username))
            return redirect('/user/' + user.username)

    return redirect(url_for('index'))

@app.route('/user/<username>/profile/newavatar', methods=['POST'])
def newavatar(username):
    pd = PageData()
    if 'username' in session:
        ret = False
        if request.method == 'POST':
            try:
                user = SiteUser.create(session['username'])
                profile = user.profile()
            except NoUser:
                return render_template('error.html', pd=pd)

            raw = request.files['img'].read()

            size = len(raw)
            if size > 2097152:
                logger.info('rejected avatar for {}, raw size is {}'.format(username, size))
                flash("Please resize your avatar to be smaller than 2MB. The image you uploaded was {:.1f}MB".format(size / 1000000.0))
                return redirect('/user/' + user.username)

            if not imghdr.what(None, raw):
                flash("There was a problem updating your avatar.")
                logger.info('failed to update avatar for {} '.format(username))
                return redirect('/user/' + user.username)

            image = base64.b64encode(raw)
 
            profile.profile['avatar'] = image
            profile.update()

            flash("Your avatar has been updated.")
            logger.info('avatar updated for for {}, raw size is {}'.format(username, size))
            return redirect('/user/' + user.username)

    return redirect(url_for('index'))

@app.route('/user/<username>/avatar')
def serve_avatar(username):
    try:
        user = SiteUser.create(username)
        avatar = user.profile().profile['avatar']

        resp = make_response(base64.b64decode(avatar))
        resp.content_type = "image/png"
        return resp
    except (IOError, NoUser):
        return page_not_found(404)

from core.memoize import memoize_with_expiry
tzcache = dict()
@memoize_with_expiry(tzcache, 99999999999)
def get_timezones():
    timezones = dict()
    for timezone in pytz.common_timezones:
        offset = datetime.datetime.now(pytz.timezone(timezone)).strftime('%z')
        key = '{} {}'.format(offset, timezone)
        timezones[key] = timezone
    return timezones

@app.route('/user/<username>')
def show_user_profile(username):
    pd = PageData()
    pd.title = "Profile for " + username
    pd.timezones = get_timezones()

    try:
        pd.profileuser = SiteUser.create(username)
    except NoUser:
        return page_not_found(404)

    return render_template('profile.html', pd=pd)
