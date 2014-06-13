"""
fuzzable_request.py

Copyright 2006 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
import copy
import string

from urllib import unquote
from itertools import chain

import w3af.core.controllers.output_manager as om
import w3af.core.data.kb.config as cf

from w3af.core.controllers.exceptions import BaseFrameworkException
from w3af.core.data.dc.cookie import Cookie
from w3af.core.data.dc.generic.data_container import DataContainer
from w3af.core.data.dc.headers import Headers
from w3af.core.data.dc.generic.kv_container import KeyValueContainer
from w3af.core.data.dc.factory import dc_factory
from w3af.core.data.dc.form import Form
from w3af.core.data.db.disk_item import DiskItem
from w3af.core.data.parsers.url import URL
from w3af.core.data.request.request_mixin import RequestMixIn


ALL_CHARS = ''.join(chr(i) for i in xrange(256))
TRANS_TABLE = string.maketrans(ALL_CHARS, ALL_CHARS)
DELETE_CHARS = ''.join(['\\', "'", '"', '+', ' ', chr(0), chr(int("0D", 16)),
                       chr(int("0A", 16))])


TYPE_ERROR = 'FuzzableRequest __init__ parameter %s needs to be of %s type'


class FuzzableRequest(RequestMixIn, DiskItem):
    """
    This class represents a fuzzable request. Fuzzable requests were created
    to allow w3af plugins to be much simpler and don't really care if the
    vulnerability is in the postdata, querystring, header, cookie or any other
    injection point.

    FuzzableRequest classes are just an easy to use representation of an HTTP
    Request, which will (during the audit phase) be wrapped into a Mutant
    and have its values modified.

    :author: Andres Riancho (andres.riancho@gmail.com)
    """
    # In most cases we don't care about these headers, even if provided by the
    # user, since they will be calculated based on the attributes we are
    # going to store and these won't be updated.
    REMOVE_HEADERS = ('content-length',)

    def __init__(self, uri, method='GET', headers=None, cookie=None,
                 post_data=None):
        super(FuzzableRequest, self).__init__()

        # Note: Do not check for the URI/Headers type here, since I'm doing it
        # in set_uri() and set_headers() already.
        if cookie is not None and not isinstance(cookie, Cookie):
            raise TypeError(TYPE_ERROR % ('cookie', 'Cookie'))

        if post_data is not None and not isinstance(post_data, DataContainer):
            raise TypeError(TYPE_ERROR % ('post_data', 'DataContainer'))

        # Internal variables
        self._method = method
        self._cookie = Cookie() if cookie is None else cookie
        self._post_data = KeyValueContainer() if post_data is None else post_data

        # Set the headers
        self._headers = None
        pheaders = Headers() if headers is None else headers
        self.set_headers(pheaders)

        # Set the URL
        self._uri = None
        self._url = None
        self.set_uri(uri)

        # Set the internal variables
        self._form = None
        self._sent_info_comp = None

    def get_default_headers(self):
        """
        :return: The headers we want to use framework-wide for fuzzing. By
                 default we set the fuzzable_headers to [], which makes this
                 method return an empty Headers instance.

                 When the user sets a fuzzable_headers it will create a Headers
                 instance with empty values.

                 We then append the specific headers supplied for this
                 FuzzableRequest instance to the default headers. Any specific
                 headers override the default (empty) ones.
        """
        fuzzable_headers = cf.cf.get('fuzzable_headers') or []
        req_headers = [(h, '') for h in fuzzable_headers]
        return Headers(init_val=req_headers)

    @classmethod
    def from_parts(cls, url, method='GET', post_data=None, headers=None):
        """
        :return: An instance of FuzzableRequest from the provided parameters.
        """
        if isinstance(url, basestring):
            url = URL(url)

        if isinstance(post_data, basestring):
            post_data = dc_factory(headers, post_data)

        return cls(url, method=method, headers=headers, post_data=post_data)

    @classmethod
    def from_http_response(cls, http_response):
        """
        :return: An instance of FuzzableRequest using the URL and cookie from
                 the http_response. The method used is "GET", and no post_data
                 is set.
        """
        cookie = Cookie.from_http_response(http_response)
        return cls(http_response.get_uri(), method='GET', cookie=cookie)

    @classmethod
    def from_form(cls, form, headers=None):
        if form.get_method().upper() == 'POST':
            r = cls(form.get_action(),
                    method=form.get_method(),
                    headers=headers,
                    post_data=form)
        else:
            # The default is a GET request
            r = cls(form.get_action(),
                    method=form.get_method(),
                    headers=headers)

        r.set_form(form)

        return r

    def set_form(self, form):
        """
        :see: Comment on get_form()
        """
        if not isinstance(form, Form):
            raise TypeError('Expected Form instance.')

        if form is not self.get_raw_data():
            # We're in the case where the form action is GET (see from_form)
            # Something interesting to notice is that in cases where the form
            # has an action with a querystring; and the method is GET, the
            # browser will ignore the action query-string and overwrite it
            # with the form parameters (this was tested with Chrome).
            self.set_uri(self.get_url())

            # The rest of this story continues in get_uri()

        self._form = form

    def get_form(self):
        """
        FuzzableRequests represent an HTTP request, sometimes that HTTP request
        is associated with an HTML form. When the FuzzableRequest represents
        a form which is sent over GET the parameters are sent in the query
        string:

            http://w3af.com/?id=2

        And it is retrieved by performing fr.get_url().querystring

        On the other hand, when it represents a form which is sent over POST,
        the data is sent in the post-data:

            POST / HTTP/1.1

            id=2

        And is retrieved by performing fr.get_raw_data().

        To avoid duplicated code, where I get the URL's querystring and the
        self._post_data attributes trying to find the Form instance, I'm adding
        this convenience function which retrieves the form, no matter where it
        lives.

        :return: The form (from querystring or post-data), None if this instance
                 is not related with a Form object.
        """
        return self._form

    def export(self):
        """
        Generic version of how fuzzable requests are exported:
            METHOD,URL,POST_DATA

        Example:
            GET,http://localhost/index.php?abc=123&def=789,
            POST,http://localhost/index.php,abc=123&def=789

        :return: a csv str representation of the request
        """
        #
        # TODO: Why don't we export headers and cookies?
        #
        output = []

        for data in (self.get_method(), self.get_uri(), self._post_data):
            output.append('"%s"' % data)

        return ','.join(output)

    def sent(self, smth_instng):
        """
        Checks if something similar to `smth_instng` was sent in the request.
        This is used to remove false positives, e.g. if a grep plugin finds a
        "strange" string and wants to be sure it was not generated by an audit
        plugin.

        This method should only be used by grep plugins which often have false
        positives.

        The following example shows that we sent d'z"0 but d\'z"0 will
        as well be recognised as sent

        TODO: This function is called MANY times, and under some circumstances
        it's performance REALLY matters. We need to review this function.

        :param smth_instng: The string
        :return: True if something similar was sent
        """
        def make_comp(heterogen_string):
            """
            This basically removes characters that are hard to compare
            """
            return string.translate(heterogen_string.encode('utf-8'),
                                    TRANS_TABLE, deletions=DELETE_CHARS)

        data = self.get_data()
        # This is the easy part. If it was exactly like this in the request
        if data and smth_instng in data or \
        smth_instng in self.get_uri() or \
        smth_instng in unquote(data) or \
        smth_instng in unicode(self.get_uri().url_decode()):
            return True

        # Ok, it's not in it but maybe something similar
        # Let's set up something we can compare
        if self._sent_info_comp is None:
            data_encoding = self._post_data.encoding
            post_data = str(self.get_data())
            dec_post_data = unquote(post_data).decode(data_encoding)

            data = u'%s%s%s' % (unicode(self.get_uri()), data, dec_post_data)

            self._sent_info_comp = make_comp(data + unquote(data))

        min_len = 3
        # make the smth_instng comparable
        smth_instng_comps = (make_comp(smth_instng),
                             make_comp(unquote(smth_instng)))
        for smth_intstng_comp in smth_instng_comps:
            # We don't want false negatives just because the string is
            # short after making comparable
            if smth_intstng_comp in self._sent_info_comp and \
            len(smth_intstng_comp) >= min_len:
                return True

        # I didn't sent the smth_instng in any way
        return False

    def __hash__(self):
        return hash(str(self.get_uri()) + self.get_data())

    def __str__(self):
        """
        :return: A string representation of this fuzzable request.
        """
        fmt = '%s | Method: %s | %s parameters: (%s)'

        if self.get_raw_data():
            parameters = self.get_raw_data().get_param_names()
            dc_type = self.get_raw_data().get_type()
        else:
            parameters = self.get_uri().querystring.get_param_names()
            dc_type = self.get_uri().querystring.get_type()

        return fmt % (self.get_url(), self.get_method(), dc_type,
                      ','.join(parameters))

    def __repr__(self):
        return '<fuzzable request | %s | %s>' % (self.get_method(),
                                                 self.get_uri())

    def __eq__(self, other):
        """
        Two requests are equal if:
            - They have the same URL
            - They have the same method
            - They have the same parameters
            - The values for each parameter is equal

        :return: True if the requests are equal.
        """
        if isinstance(other, FuzzableRequest):
            return (self.get_method() == other.get_method() and
                    self.get_uri() == other.get_uri() and
                    self.get_data() == other.get_data() and
                    self.get_headers() == other.get_headers())

        return False

    def get_eq_attrs(self):
        return ['_method', '_uri', '_post_data', '_headers']

    def __ne__(self, other):
        return not self.__eq__(other)

    def is_variant_of(self, other):
        """
        Two requests are loosely equal (or variants) if:
            - They have the same URL
            - They have the same HTTP method
            - They have the same parameter names
            - The values for each parameter have the same type (int / string)

        :return: True if self and other are variants.
        """
        if self.get_method() != other.get_method():
            return False

        if self.get_url() != other.get_url():
            return False

        self_qs = self.get_uri().querystring
        other_qs = other.get_uri().querystring

        if not self_qs.is_variant_of(other_qs):
            return False

        return True

    def set_url(self, url):
        if not isinstance(url, URL):
            raise TypeError('The "url" parameter of a %s must be of '
                            'url.URL type.' % type(self).__name__)

        self._url = URL(url.url_string.replace(' ', '%20'))
        self._uri = self._url

    def set_uri(self, uri):
        if not isinstance(uri, URL):
            raise TypeError('The "uri" parameter of a %s must be of '
                            'url.URL type.' % type(self).__name__)
        self._uri = uri
        self._url = uri.uri2url()

    def get_querystring(self):
        return self.get_uri().querystring

    def set_querystring(self, new_qs):
        self.get_uri().querystring = new_qs

    def set_method(self, method):
        self._method = method

    def set_headers(self, headers):
        if headers is not None and not isinstance(headers, Headers):
            raise TypeError(TYPE_ERROR % ('headers', 'Headers'))

        for header_name in self.REMOVE_HEADERS:
            try:
                headers.idel(header_name)
            except KeyError:
                # We don't care if they don't exist
                pass

        self._headers = headers

    def set_referer(self, referer):
        self._headers['Referer'] = str(referer)

    def set_cookie(self, c):
        """
        :param cookie: A Cookie object as defined in core.data.dc.cookie,
            or a string.
        """
        if isinstance(c, Cookie):
            self._cookie = c
        elif isinstance(c, basestring):
            self._cookie = Cookie(c)
        elif c is None:
            self._cookie = Cookie()
        else:
            fmt = '[FuzzableRequest error] set_cookie received: "%s": "%s".'
            error_str = fmt % (type(c), repr(c))
            om.out.error(error_str)
            raise BaseFrameworkException(error_str)

    def get_url(self):
        return self._url

    def get_uri(self):
        """
        :see: Comment in get_form()
        :return: The URI to send in the HTTP request
        """
        if self._form is None:
            # This is the most common case, where the FuzzableRequest wasn't
            # created using .from_form()
            return self._uri

        if self._post_data:
            # This is the case where the instance was created using .from_form()
            # but it is a POST form
            return self._uri

        # This is the case where the instance was created using .from_form() and
        # we need to append the form information into the URI
        uri = self._uri.copy()
        uri.querystring = self._form
        return uri

    def set_data(self, post_data):
        """
        Set the DataContainer which we'll use for post-data
        """
        if not isinstance(post_data, DataContainer):
            raise TypeError('The "post_data" parameter of a %s must be of '
                            'DataContainer type.' % type(self).__name__)
        self._post_data = post_data

    def get_data(self):
        """
        The data is the string representation of the post data, in most
        cases it will be used as the POSTDATA for requests.
        """
        return str(self._post_data)

    def get_raw_data(self):
        return self._post_data

    def get_method(self):
        return self._method

    def get_post_data_headers(self):
        """
        :return: A Headers object with the headers required to send the
                 self._post_data to the wire. For example, if the data is
                 url-encoded:
                    a=3&b=2

                 This method returns:
                    Content-Length: 7
                    Content-Type: application/x-www-form-urlencoded

                 When someone queries this object for the headers using
                 get_headers(), we'll include these. Hopefully this means that
                 the required headers will make it to the wire.
        """
        return Headers(init_val=self.get_raw_data().get_headers())

    def get_headers(self):
        """
        :return: Calls get_default_headers to get the default framework headers,
        overwrites any overlap with specific_headers and returns a Headers
        instance
        """
        for k, v in chain(self.get_post_data_headers().items(),
                          self.get_default_headers().items()):
            # Ignore any keys which are already defined in the user-specified
            # headers
            kvalue, kreal = self._headers.iget(k, None)
            if kvalue is not None:
                continue

            self._headers[k] = v

        return self._headers

    def get_referer(self):
        return self.get_headers().get('Referer', None)

    def get_cookie(self):
        return self._cookie

    def get_file_vars(self):
        """
        :return: A list of postdata parameters that contain a file
        """
        try:
            return self._post_data.get_file_vars()
        except AttributeError:
            return []

    def copy(self):
        return copy.deepcopy(self)
