import hashlib
import os
import datetime
import time
import uuid
import imghdr

from scarf import app
from flask import request, redirect, session, escape, flash, url_for
from urlparse import urlparse, urljoin
from sql import upsert, doupsert, read, doquery, delete

from memoize import memoize_with_expiry, cache_persist, long_cache_persist

from config import upload_dir

########## Utility stuff

# Workaround for the issue identified here:
# https://bugs.python.org/issue16512
# Credit to:
# https://coderwall.com/p/btbwlq/fix-imghdr-what-being-unable-to-detect-jpegs-with-icc_profile
def test_icc_profile_images(h, f):
    if h.startswith('\xff\xd8') and h[6:17] == b'ICC_PROFILE':
        return "jpeg"

imghdr.tests.append(test_icc_profile_images)

class pagedata:
    accesslevels = {-1: 'anonymous', 0:'banned', 1:'user', 10:'moderator', 255:'admin'}
    pass

    def __init__(self):
        if 'username' in session:
            self.authuser = siteuser.create(escape(session['username']))
            try:
                self.authuser = siteuser.create(escape(session['username']))
            except:
                pass

######### User stuff
stats_cache = dict()

@memoize_with_expiry(stats_cache, long_cache_persist)
def get_whores_table():
    sql = """select count(*), users.username
             from users 
             join ownwant on ownwant.userid=users.uid 
             where ownwant.own = 1 
             group by users.uid, ownwant.own 
             order by count(*) desc limit 50;"""
    result = doquery(sql)

    return result;

@memoize_with_expiry(stats_cache, long_cache_persist)
def get_contribs_table():
    sql = """select count(*), users.username
             from users 
             join userstat_uploads on userstat_uploads.uid=users.uid 
             group by users.uid, userstat_uploads.uid
             order by count(*) desc limit 50;"""
    result = doquery(sql)

    return result;

@memoize_with_expiry(stats_cache, long_cache_persist)
def user_by_uid(uid):
    sql = read('users', **{"uid": uid})
    result = doquery(sql)

    try:
        return result[0][1]
    except IndexError:
        return

@memoize_with_expiry(stats_cache, long_cache_persist)
def uid_by_user(username):
    sql = read('users', **{"username": username})
    result = doquery(sql)

    try:
        return result[0][0]
    except IndexError:
        return

class NoUser(Exception):
    def __init__(self, username):
        Exception.__init__(self, username)

class AuthFail(Exception):
    def __init__(self, username):
        Exception.__init__(self, username)

siteuser_cache = dict()
class siteuser:
 
    @classmethod
    @memoize_with_expiry(siteuser_cache, cache_persist)
    def create(cls, username):
        return cls(username)

    def __init__(self, username):
        self.collection = []
        self.messages = []
        self.contribs = []

        self.auth = False
        self.username = username

        sql = """select users.uid, users.pwhash, users.pwsalt, users.email, users.joined, userstat_lastseen.date, users.accesslevel 
                 from users
                 join userstat_lastseen on userstat_lastseen.uid=users.uid 
                 where users.username = "%s"; """ % username
        result = doquery(sql)

        try:
            self.uid = result[0][0]
            # self.username = result[0][1]
            self.pwhash = result[0][1]
            self.pwsalt = result[0][2]
            self.email = result[0][3]
            self.joined = result[0][4]
            self.lastseen = result[0][5]
            self.accesslevel = result[0][6]
        except IndexError:
            raise NoUser(username)
        except TypeError:
            pass

        sql = """select count(*), users.username
                 from userstat_uploads 
                 join users on userstat_uploads.uid=users.uid 
                 where userstat_uploads.uid = "%s"
                 group by users.uid, userstat_uploads.uid
                 order by count(*) desc limit 50;""" % self.uid
        result = doquery(sql)

        try:
            self.numadds = result[0][0]
        except IndexError:
            self.numadds = 0

        # Update lastseen if we're looking up the currently logged in user
        if 'username' in session:
            if session['username'] is username:
                self.seen()
                self.auth = True

        sql = """select items.name
                 from items
                 join userstat_uploads on userstat_uploads.itemid=items.uid
                 where userstat_uploads.uid=%s""" % self.uid

        result = doquery(sql)

        for item in result:
            self.contribs.append(item[0])

    def pop_collection(self):
        if not self.collection:
            sql = """select ownwant.own, ownwant.willtrade, ownwant.want, ownwant.hidden, items.name
                     from ownwant
                     join items on items.uid=ownwant.itemid
                     where ownwant.userid=%s""" % self.uid

            result = doquery(sql)

            for item in result:
                # TODO use ownwant object here, remember to document the change
                sitem = siteitem(item[4])
                sitem.have = item[0]
                sitem.willtrade = item[1]
                sitem.want = item[2]
                sitem.hidden = item[3]

                self.collection.append(sitem)

    def query_collection(self, item):
        class __ownwant__:
            def __init__(self):
                self.have = 0
                self.want = 0
                self.willtrade = 0
                self.hidden = 0
                pass

        ret = __ownwant__()

        try:
            sql = read('items', **{"name": item})
            sresult = doquery(sql)
     
            sql = read('ownwant', **{"userid": self.uid, "itemid": sresult[0][0]})
            result = doquery(sql)

            ret.uid = result[0][0]
            ret.have = result[0][3]
            ret.willtrade = result[0][4]
            ret.want = result[0][5]
            ret.hidden = result[0][6]
        except IndexError:
            pass

        return ret

    def pop_messages(self):
        if not self.messages:
            sql = """select * from messages
                     where fromuserid = '%s' or touserid = '%s'""" % (self.uid, self.uid)

            result = doquery(sql)

            for item in result:
                if item[4] >= messagestatus['unread_pm']:
                    pm = pmessage.create(item[0])
                else:
                    pm = trademessage.create(item[0])

                pm.load_replies()
                self.messages.append(pm)

    def seen(self):
        self.lastseen=datetime.datetime.now()

        sql = upsert("userstat_lastseen", \
                     uid=self.uid, \
                     date=self.lastseen)
        result = doupsert(sql)

    def authenticate(self, password):
        if self.accesslevel == 0:
            flash('Your account has been banned')
        else:
            checkhash = gen_pwhash(password, self.pwsalt)
        
            if checkhash == self.pwhash:
                session['username'] = self.username
            else:
                raise AuthFail(self.username)

    def newaccesslevel(self, accesslevel):
        self.accesslevel = int(accesslevel)

        sql = upsert("users", 
                     uid=self.uid, 
                     accesslevel=self.accesslevel)
        data = doupsert(sql)

    def newpassword(self, password):
        self.pwsalt = str(uuid.uuid4().get_hex().upper()[0:6])
        self.pwhash = gen_pwhash(password, self.pwsalt)

        sql = upsert("users", 
                     uid=self.uid, 
                     pwsalt=self.pwsalt,
                     pwhash=self.pwhash)
        data = doupsert(sql)

    def newemail(self, email):
        self.email = email

        sql = upsert("users", 
                     uid=self.uid, 
                     email=self.email)
        data = doupsert(sql)

def gen_pwhash(password, salt):
    return hashlib.sha224(password + salt).hexdigest()

def new_user(username, password, email):
    try:
        salt=str(uuid.uuid4().get_hex().upper()[0:6])
        sql = upsert("users", \
                     uid=0, \
                     username=username, \
                     pwhash=gen_pwhash(password, salt), \
                     pwsalt=salt, \
                     email=email, \
                     joined=datetime.datetime.now(), \
                     accesslevel=1)
        uid = doupsert(sql)

        sql = upsert("userstat_uploads", \
                     uid=uid, \
                     count=0)
        result = doupsert(sql)

        sql = upsert("userstat_lastseen", \
                     uid=uid, \
                     date=datetime.datetime.now())
        result = doupsert(sql)
 
    except Exception as e:
        return False

    return True

######### Image stuff

class NoImage(Exception):
    def __init__(self, item):
        Exception.__init__(self, item)

class siteimage:
    # add factory and caching

    def __init__(self, uid):
        sql = read('images', **{"uid": uid})
        result = doquery(sql)

        try: 
            self.uid = result[0][0]
            self.filename = result[0][1]
            self.tag = result[0][2]
        except IndexError:
            raise NoImage(uid)

    def delete(self):
        #TODO image purgatory
        sql = delete('itemimg', **{"imgid": self.uid})
        result = doquery(sql)

        sql = delete('images', **{"uid": self.uid})
        result = doquery(sql)

        sql = delete('imgmods', **{"imgid": self.uid})
        result = doquery(sql)

        try:
            os.remove(upload_dir + self.filename)
        except Exception as e:
            raise

    def approve(self):
        sql = delete('imgmods', **{"imgid": self.uid})
        result = doquery(sql)

    def flag(self):
        if 'username' in session:
            username = session['username']
        else:
            username = "anon"

        sql = upsert('imgmods', **{"imgid": self.uid, "username": username, "flag": 1})
        result = doquery(sql)

######### Item stuff

item_cache = dict()

@memoize_with_expiry(item_cache, long_cache_persist)
def item_by_uid(uid):
    sql = read('items', **{"uid": uid})
    result = doquery(sql)

    try:
        return result[0][1]
    except IndexError:
        return

class NoItem(Exception):
    def __init__(self, item):
        Exception.__init__(self, item)

class __siteitem__:
    def __init__(self):
        self.name = ""
        self.have = 0
        self.want = 0
        self.willtrade = 0

class siteitem(__siteitem__):
    def __init__(self, name):
        self.name = name
        self.images = []
        self.haveusers = []
        self.wantusers = []
        self.willtradeusers = []

        sql = read('items', **{"name": name})
        result = doquery(sql)

        try:
            self.uid = result[0][0]
            #self.name = result[0][1]
            self.description = result[0][2]
            self.added = result[0][3]
            self.modified = result[0][4]
        except IndexError:
            raise NoItem(name)

        sql = read('itemimg', **{"itemid": self.uid})
        result = doquery(sql)

        try:
            for itemimg in result:
                image = siteimage(itemimg[1])
                self.images.append(image)
        except IndexError:
            pass

        # FIXME check for hidden?
        sql = read('ownwant', **{"itemid": self.uid, "own": "1"})
        res = doquery(sql)
        self.have = len(res)
        for user in res:
            sql = read('users', **{"uid": user[1]})
            result = doquery(sql)
            userinfo = siteuser.create(result[0][1])
            self.haveusers.append(userinfo)

        sql = read('ownwant', **{"itemid": self.uid, "want": "1"})
        res = doquery(sql)
        self.want = len(res)
        for user in res:
            sql = read('users', **{"uid": user[1]})
            result = doquery(sql)
            userinfo = siteuser.create(result[0][1])
            self.wantusers.append(userinfo)

        sql = read('ownwant', **{"itemid": self.uid, "willtrade": "1"})
        res = doquery(sql)
        self.willtrade = len(res)
        for user in res:
            sql = read('users', **{"uid": user[1]})
            result = doquery(sql)
            userinfo = siteuser.create(result[0][1])
            self.willtradeusers.append(userinfo)

    def delete(self):
        for i in self.images: 
            delimg = siteimage(escape(i.uid))
            delimg.delete()
     
        sql = delete('items', **{"uid": self.uid}) 
        result = doquery(sql) 
     
        sql = delete('itemimg', **{"itemid": self.uid}) 
        result = doquery(sql) 
     
        sql = delete('ownwant', **{"itemid": self.uid}) 
        result = doquery(sql) 

        # Instead of deleting maybe we should replace the item id with something to point to an "unknown item" page
        sql = delete('tradelist', **{"itemid": self.uid}) 
        result = doquery(sql) 

    def newimg(self, f, tag):
        if not f.filename == '':
            fuuid = uuid.uuid4().get_hex()
            try:
                newname = fuuid + os.path.splitext(f.filename)[1]
                f.save(upload_dir + newname)
            except Exception as e:
                raise

            if imghdr.what(upload_dir + newname):
                sql = upsert("images", \
                             uid=0, \
                             filename=newname, \
                             tag=escape(tag))
                imgid = doupsert(sql)

                sql = upsert("itemimg", \
                             imgid=imgid, \
                             itemid=self.uid)
                data = doupsert(sql)

                try:
                    username = session['username']
                except KeyError:
                    username = "anon"

                sql = upsert("imgmods", \
                             username=username, \
                             imgid=imgid)
                data = doupsert(sql)

                flash('Uploaded ' + f.filename)
                return True
            else:
                try:
                    os.remove(upload_dir + newname)
                except:
                    app.logger.error("Error removing failed image upload: " + upload_dir + newname)

                flash(f.filename + " is not an image.")
                return False

def new_item(name, description, userid):
    sql = upsert("items", \
                 uid=0, \
                 name=name, \
                 description=description, \
                 added=datetime.datetime.now(), \
                 modified=datetime.datetime.now())

    data = doupsert(sql)

    if userid is not 0:
        sql = upsert("userstat_uploads", 
                     uid=userid, 
                     itemid=data)
        data = doupsert(sql)


@memoize_with_expiry(item_cache, long_cache_persist)
def all_items():
    items = []

    try:
        sql = read('items')
        result = doquery(sql)

        for item in result:
            newitem = __siteitem__()
            newitem.uid = item[0]
            newitem.name = item[1]
            newitem.description = item[2]
            newitem.added = item[3]
            newitem.modified = item[4]

            items.append(newitem)
    except TypeError:
        pass

    return items

######### Redirect stuff

def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and \
           ref_url.netloc == test_url.netloc

def redirect_back(endpoint, **values):
    target = request.referrer
    if not target or not is_safe_url(target):
        target = url_for(endpoint, **values)
    return redirect(target)


# Trade and message stuff

messagestatus = {'unread_trade': 0, 'active_trade': 1, 'complete_trade': 2, 'settled_trade': 3, 'rejected_trade': 4, 'unread_pm': 10, 'read_pm': 11}
tradestatus = {'unmarked': 0, 'rejected': 1, 'accepted': 2}

pmessage_cache = dict()
class pmessage:
    @classmethod
    @memoize_with_expiry(pmessage_cache, long_cache_persist)
    def create(cls, messageid):
        return cls(messageid)

    def __init__(self, messageid):
        self.messagestatus = messagestatus

        sql = read('messages', **{"uid": messageid})
        result = doquery(sql)

        try:
            self.uid = result[0][0]
            self.from_uid = result[0][1]
            self.to_uid = result[0][2]
            self.subject = result[0][3]
            self.message = result[0][4]
            self.status = result[0][5]
            self.parentid = result[0][6]
            self.sent = result[0][7]

            self.from_user = siteuser.create(user_by_uid(self.from_uid)).username
            self.to_user = siteuser.create(user_by_uid(self.to_uid)).username

            if self.parentid > 0:
                self.parent = pmessage.create(self.parentid)

            self.replies = []

        except IndexError:
            self.uid = 0


    def read(self):
        if self.uid > 0 and self.status == messagestatus['unread_pm'] and uid_by_user(session['username']) == self.to_uid:
            sql = upsert("messages", \
                         uid=self.uid, \
                         status=messagestatus['read_pm'])
            data = doupsert(sql)
        else:
            return

    def unread(self):
        if self.uid > 0 and self.status == messagestatus['read_pm'] and uid_by_user(session['username']) == self.to_uid:
            sql = upsert("messages", \
                         uid=self.uid, \
                         status=messagestatus['unread_pm'])
            data = doupsert(sql)
        else:
            return

    @memoize_with_expiry(pmessage_cache, cache_persist)
    def load_replies(self):
        if not self.replies:
            sql = read('messages', **{"parent": self.uid})
            result = doquery(sql)

            for reply in result:
                pm = pmessage.create(reply[0])
                pm.load_replies()
                self.replies.append(pm)

class tradeitem:
    def __init__(self, itemid):
        self.uid = itemid 
        self.itemid = 0
        self.messageid = 0
        self.userid = 0
        self.acceptstatus = 0

    def accept(self):
        if self.uid > 0:
            sql = upsert("tradelist", \
                         uid=self.uid, \
                         acceptstatus=tradestatus['accepted'])
            data = doupsert(sql)
        else:
            return

    def reject(self):
        if self.uid > 0:
            sql = upsert("tradelist", \
                         uid=self.uid, \
                         acceptstatus=tradestatus['rejected'])
            data = doupsert(sql)
        else:
            return

#FIXME inheritance
class trademessage(pmessage):
    cache = []

    @classmethod
    def create(cls, messageid):
        for o in trademessage.cache:
            if o.uid == messageid:
                return o

        o = cls(messageid)
        cls.cache.append(o)
        return o

    def __init__(self, messageid):
        self.messagestatus = messagestatus
        self.tradestatus = tradestatus

        sql = read('messages', **{"uid": messageid})
        result = doquery(sql)

        try:
            self.uid = result[0][0]
            self.from_uid = result[0][1]
            self.to_uid = result[0][2]
            self.subject = result[0][3]
            self.message = result[0][4]
            self.status = result[0][5]
            self.parentid = result[0][6]
            self.sent = result[0][7]

            self.from_user = siteuser.create(user_by_uid(self.from_uid)).username
            self.to_user = siteuser.create(user_by_uid(self.to_uid)).username

            if self.parentid > 0:
                self.parent = pmessage.create(self.parentid)

            self.replies = []

        except IndexError:
            self.uid = 0

        self.items = []

        sql = read('tradelist', **{"messageid": messageid})
        result = doquery(sql)

        complete = True
        for item in result:
            ti = tradeitem(item[0])
            ti.itemid = item[1]
            ti.messageid = item[2]
            ti.userid = item[3]
            ti.acceptstatus = item[4]
            ti.item = siteitem(item_by_uid(ti.itemid))
            ti.user = siteuser.create(user_by_uid(ti.userid))

            self.items.append(ti)

            if (ti.acceptstatus != tradestatus['accepted']):
                complete = False

        if complete == True and self.status < messagestatus['settled_trade']:
            self.status = messagestatus['complete_trade']

    def settle(self):
        if self.uid > 0:
            sql = upsert("messages", \
                         uid=self.uid, \
                         status=messagestatus['settled_trade'])
            data = doupsert(sql)
        else:
            return

    def reject(self):
        if self.uid > 0:
            sql = upsert("messages", \
                         uid=self.uid, \
                         status=messagestatus['rejected_trade'])
            data = doupsert(sql)
        else:
            return

def send_pm(fromuserid, touserid, subject, message, status, parent):
    """
    TODO: validation to ensure someone didn't fuck with the form
          and reparent their message to someone else's
    """

    try:
        sql = upsert("messages", \
                     uid=0, \
                     fromuserid=fromuserid, \
                     touserid=touserid, \
                     subject=subject, \
                     message=message, \
                     parent=parent, \
                     sent=datetime.datetime.now(), \
                     status=status)
        data = doupsert(sql)
    except Exception as e:
        raise

    return data

def add_tradeitem(itemid, messageid, userid, acceptstatus):
    try:
        sql = upsert("tradelist", \
                     uid=0, \
                     itemid=itemid, \
                     messageid=messageid, \
                     userid=userid, \
                     acceptstatus=acceptstatus)
        data = doupsert(sql)
    except Exception as e:
        return False

    return True

