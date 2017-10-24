#!/usr/bin/python
import re

try:
    from bs4 import BeautifulSoup
except ImportError:
    HAS_BEAUTIFULSOUP = False
else:
    HAS_BEAUTIFULSOUP = True

# balancer member attributes extraction regexp: the exp inside "()" is catch expression. where each expression inside is stored as a member of list ['abc','def']
EXPRESSION = r"(b=([\w\.\-]+)&w=(https?|ajp|wss?|ftp|[sf]cgi)://([\w\.\-]+):?(\d*)([/\w\.\-]*)&?[\w\-\=]*)"
# Apache2 server version extraction regexp:
APACHE_VERSION_EXPRESSION = r"Server Version: Apache/([\d.]+) \(([\w]+)\)"


def regexp_extraction(string, _regexp, groups=1): # to extract the regex
    """ Returns the capture group (default=1) specified in the regexp, applied to the string """
    regexp_search = re.search(string=str(string), pattern=str(_regexp))
    if regexp_search:
        if regexp_search.group(groups) != '':
            return str(regexp_search.group(groups))
    return None


class BalancerMember(object):
    """ Apache 2.4 mod_proxy LB balancer member.
    attributes:
        read-only:
            host -> member host (string),
            management_url -> member management url (string),
            protocol -> member protocol (string)
            port -> member port (string),
            path -> member location (string),
            balancer_url -> url of this member's parent balancer (string),
            attributes -> whole member attributes (dictionary)
            module -> ansible module instance (AnsibleModule object).
        writable:
            status -> status of the member (dictionary)
    """
    from ansible.module_utils.basic import AnsibleModule
    global_bal_page = ""
    global_module = AnsibleModule(
        argument_spec=dict(
            balancer_vhost=dict(required=True, default=None, type='str'),
            balancer_url_suffix=dict(default="/balancer-manager/", type='str'),
            member_host=dict(type='str'),
            state=dict(type='str'),
            tls=dict(default=False, type='bool'),
            validate_certs=dict(default=True, type='bool'),
            url_username=dict(default=None, required=False),
            url_password=dict(default=None, no_log=True)
        ),
        supports_check_mode=True
    )

    def __init__(self, management_url, balancer_url, module, soup):
        self.host = regexp_extraction(management_url, str(EXPRESSION), 4)
        self.management_url = str(management_url)
        self.protocol = regexp_extraction(management_url, EXPRESSION, 3)
        self.port = regexp_extraction(management_url, EXPRESSION, 5)
        self.path = regexp_extraction(management_url, EXPRESSION, 6)
        self.balancer_url = str(balancer_url)
        self.module = module
	self.soup = soup
        BalancerMember.global_module = module
        if self.module.params['member_host'] is None:
            self.attributes = self.get_member_attributes()

    def get_member_attributes(self):
        """ Returns a dictionary of a balancer member's attributes."""

        balancer_member_page = fetch_url(self.module, self.management_url)
	BalancerMember.global_bal_page = balancer_member_page

        try:
            assert balancer_member_page[1]['status'] == 200
        except AssertionError:
            self.module.fail_json(msg="Could not get balancer_member_page, check for connectivity! " + balancer_member_page[1])
        else:
            try:
                if self.soup is None:
                    soup = BeautifulSoup(balancer_member_page[0], "lxml")
                else:
                    soup = self.soup
		#soup = BeautifulSoup(balancer_member_page[0])
            except TypeError:
                    self.module.fail_json(msg="Cannot parse balancer_member_page HTML! " + str(soup))
            else:
	        subsoup = soup.findAll('table')[1].findAll('tr')
	        keys = subsoup[0].findAll('th')
	        for line in soup.findAll('table'):
                    line2 = line.findAll('tr')
                    for line3 in line2:
                        if re.search(pattern=str(self.host), string=str(line3)):
                            values = line3.findAll('td')
                            return dict((keys[x].string, values[x].string) for x in range(0, len(keys)))

    def get_member_status(self):
        """ Returns a dictionary of a balancer member's status attributes."""
        status_mapping = {'disabled': 'Dis'}
				
        status = {}
        actual_status = str(self.attributes['Status'])
        for mode in status_mapping.keys():
            if re.search(pattern=status_mapping[mode], string=actual_status):
                status[mode] = True
            else:
                status[mode] = False
        return status

    def set_member_status(self, values):
        """ Sets a balancer member's status attributes amongst pre-mapped values."""
        values_mapping = {'disabled': '&dw'}
	temp_url = self.management_url	
        request_body = regexp_extraction(self.management_url, EXPRESSION, 1)
	response1 = BalancerMember.global_bal_page
	soup_response1 = BeautifulSoup(str(response1), "lxml")
	array_response1 = soup_response1.findAll('input')
	string_response1 = ""
	replace_response1 = ""
	for line_response1 in array_response1[0:4]:
		if re.search(pattern='^<input name',string=str(line_response1)):
			regex_response1 = re.search('name\=\"(\w*?)\".*?value\=\"(\w*?)\"', str(line_response1))
			string_response1 = string_response1 + regex_response1.group(1) + "=" + regex_response1.group(2) + "&"

        for k in values_mapping.keys():
            if values[str(k)]:
		replace_response1 = "?" + string_response1 + "dw=Disable&"
		temp_url = temp_url.replace("?",replace_response1)
            else:
		replace_response1 = "?" + string_response1 + "dw=Enable&"
		temp_url = temp_url.replace("?",replace_response1)

        response = fetch_url(self.module, temp_url, method="GET")
	#response = urllib.urlopen(temp_url)
        try:
            assert response[1]['status'] == 200
        except AssertionError:
            self.module.fail_json(msg="Could not set the member status! " + self.host + " " + response[1]['status'])

    if global_module.params['member_host'] is not None:
        attributes = property(get_member_attributes)
    #attributes = property(get_member_attributes)
    status = property(get_member_status, set_member_status)


class Balancer(object):
    """ Apache httpd 2.4 mod_proxy balancer object"""

    def __init__(self, host, suffix, module, members=None, tls=False):
        if tls:
            self.base_url = str(str('https://') + str(host))
            self.url = str(str('https://') + str(host) + str(suffix))
        else:
            self.base_url = str(str('http://') + str(host))
            self.url = str(str('http://') + str(host) + str(suffix))
        self.module = module
        self.page = self.fetch_balancer_page()
        if members is None:
            self._members = []

    def fetch_balancer_page(self):
        """ Returns the balancer management html page as a string for later parsing."""
        page = fetch_url(self.module, str(self.url))
        try:
            assert page[1]['status'] == 200
        except AssertionError:
            self.module.fail_json(msg="Could not get balancer page! HTTP status response: " + str(page[1]['status']))
        else:
            content = page[0].read()
            apache_version = regexp_extraction(content, APACHE_VERSION_EXPRESSION, 1)
            if not re.search(pattern=r"2\.2\.[\d]*", string=apache_version):
                self.module.fail_json(msg="This module only acts on an Apache2 2.2 instance, please check the separate module for apache 2.4, current Apache2 version: " + str(apache_version))
            return content

    def get_balancer_members(self):
        """ Returns members of the balancer as a generator object for later iteration."""
        try:
            soup = BeautifulSoup(self.page, "lxml")
        except TypeError:
            self.module.fail_json(msg="Cannot parse balancer page HTML! " + str(self.page))
        else:
            for element in soup.findAll('a')[0::1]:
                balancer_member_suffix = str(element.get('href'))
                try:
                    assert balancer_member_suffix is not ''
                except AssertionError:
                    self.module.fail_json(msg="Argument 'balancer_member_suffix' is empty!")
                else:
                    #yield BalancerMember(str(self.base_url + balancer_member_suffix), str(self.url), self.module)
                    if self.module.params['member_host'] is None:
                        yield BalancerMember(str(self.base_url + balancer_member_suffix), str(self.url),self.module, soup)
                    else:
                        if re.search(pattern=str(self.module.params['member_host']),string=str(balancer_member_suffix)):
                            yield BalancerMember(str(self.base_url + balancer_member_suffix), str(self.url), self.module, soup=None)

    members = property(get_balancer_members)


def main():
    """ Initiates module."""
    module = AnsibleModule(
        argument_spec=dict(
            balancer_vhost=dict(required=True, default=None, type='str'),
            balancer_url_suffix=dict(default="/balancer-manager/", type='str'),
            member_host=dict(type='str'),
            state=dict(type='str'),
            tls=dict(default=False, type='bool'),
            validate_certs=dict(default=True, type='bool'),
            url_username=dict(default=None, required=False),
            url_password=dict(default=None, no_log=True)
        ),
        supports_check_mode=True
    )

    if HAS_BEAUTIFULSOUP is False:
        module.fail_json(msg="python module 'BeautifulSoup' is required!")

    if module.params['state'] is not None:
        states = module.params['state'].split(',')
        if (len(states) > 1) and (("present" in states) or ("enabled" in states)):
            module.fail_json(msg="state present/enabled is mutually exclusive with other states!")
        else:
            if module.params['member_host'] is None:
                module.fail_json(msg="Exactly one member_host should be provided for which state needs to be set. else please use parameters without 'state' to query all members")
            for _state in states:
                if _state not in ['enabled', 'disabled']:
                    module.fail_json(
                        msg="State can only take values amongst 'enabled', 'disabled'."
                    )
    else:
        states = ['None']

    mybalancer = Balancer(module.params['balancer_vhost'],
                          module.params['balancer_url_suffix'],
                          module=module,
                          tls=module.params['tls'])

    if module.params['member_host'] is None:
        json_output_list = []
        for member in mybalancer.members:
            json_output_list.append({
                "host": member.host,
                "status": member.status,
                "protocol": member.protocol,
                "port": member.port,
                "path": member.path,
                "attributes": member.attributes,
                "management_url": member.management_url,
                "balancer_url": member.balancer_url
            })
        module.exit_json(
            changed=False,
            members=json_output_list
        )
    else:
        changed = False
        member_exists = False
        member_status = {'disabled': False}
        for mode in member_status.keys():
            for state in states:
                if mode == state:
                    member_status[mode] = True
                elif mode == 'disabled' and state == 'absent':
                    member_status[mode] = True

        for member in mybalancer.members:
            if str(member.host) == str(module.params['member_host']):
                member_exists = True
                if module.params['state'] is not None:
                    member_status_before = member.status
                    if not module.check_mode:
                        member_status_after = member.status = member_status
                    else:
                        member_status_after = member_status
                    if member_status_before != member_status_after:
                        changed = True
                json_output = {
                    "host": member.host,
                    "status": member.status,
                    "protocol": member.protocol,
                    "port": member.port,
                    "path": member.path,
                    "attributes": member.attributes,
                    "management_url": member.management_url,
                    "balancer_url": member.balancer_url
                }
        if member_exists:
            module.exit_json(
                changed=changed,
                member=json_output
            )
        else:
            module.fail_json(msg=str(module.params['member_host']) + ' is not a member of the balancer ' + str(module.params['balancer_vhost']) + '!')

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url
if __name__ == '__main__':
    main()
