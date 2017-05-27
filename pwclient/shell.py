#!/usr/bin/env python
# Copyright (C) 2008 Nate Case <ncase@xes-inc.com>
# Copyright (C) 2017 Stephen Finucane <stephen@that.guru>
#
# This file is part of pwclient.
#
# pwclient is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# pwclient is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pwclient; if not, see <http://www.gnu.org/licenses/>.

"""Patchwork command line client."""

from __future__ import print_function
from __future__ import unicode_literals

import os
import sys
import argparse
import subprocess
import shutil

from pwclient.checks import action_create as action_check_create
from pwclient.checks import action_info as action_check_info
from pwclient.checks import action_list as action_check_list
from pwclient.compat import xmlrpclib
from pwclient.compat import ConfigParser
from pwclient.filters import Filter
from pwclient.patches import action_apply
from pwclient.patches import action_get
from pwclient.patches import action_info
from pwclient.patches import action_list
from pwclient.patches import action_update as action_update_patch
from pwclient.patches import patch_id_from_hash
from pwclient.projects import action_list as action_projects
from pwclient.states import action_list as action_states
from pwclient.transport import Transport

# Default Patchwork remote XML-RPC server URL
# This script will check the PW_XMLRPC_URL environment variable
# for the URL to access.  If that is unspecified, it will fallback to
# the hardcoded default value specified here.
DEFAULT_URL = "http://patchwork/xmlrpc/"
CONFIG_FILE = os.path.expanduser('~/.pwclientrc')

auth_actions = ['check_create', 'update']


def main():
    hash_parser = argparse.ArgumentParser(add_help=False)
    hash_parser.add_argument(
        '-h', action='store_true',
        help='lookup patch(es) by hash instead of ID')
    hash_parser.add_argument(
        '-p', metavar='PROJECT',
        help="lookup patch in project")
    hash_parser.add_argument(
        'id', metavar='PATCH_ID', nargs='+', action='store',
        help="patch ID")

    filter_parser = argparse.ArgumentParser(add_help=False)
    filter_parser.add_argument(
        '-s', metavar='STATE',
        help="filter by patch state (e.g., 'New', 'Accepted', etc.)")
    filter_parser.add_argument(
        '-a', choices=['yes', 'no'],
        help="filter by patch archived state")
    filter_parser.add_argument(
        '-p', metavar='PROJECT',
        help="filter by project name (see 'projects' for list)")
    filter_parser.add_argument(
        '-w', metavar='WHO',
        help="filter by submitter (name, e-mail substring search)")
    filter_parser.add_argument(
        '-d', metavar='WHO',
        help="filter by delegate (name, e-mail substring search)")
    filter_parser.add_argument(
        '-n', metavar='MAX#', type=int,
        help="limit results to first n")
    filter_parser.add_argument(
        '-N', metavar='MAX#', type=int,
        help="limit results to last N")
    filter_parser.add_argument(
        '-m', metavar='MESSAGEID',
        help="filter by Message-Id")
    filter_parser.add_argument(
        '-f', metavar='FORMAT',
        help=("print output in the given format. You can use tags matching "
              "fields, e.g. %%{id}, %%{state}, or %%{msgid}."))
    filter_parser.add_argument(
        'patch_name', metavar='STR', nargs='?',
        help='substring to search for patches by name')

    action_parser = argparse.ArgumentParser(
        prog='pwclient',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""Use 'pwclient <command> --help' for more info.

To avoid unicode encode/decode errors, you should export the LANG or LC_ALL
environment variables according to the configured locales on your system. If
these variables are already set, make sure that they point to valid and
installed locales.
""",
    )

    subparsers = action_parser.add_subparsers(
        title='Commands',
    )

    apply_parser = subparsers.add_parser(
        'apply', parents=[hash_parser], conflict_handler='resolve',
        help="apply a patch in the current directory using 'patch -p1'")
    apply_parser.set_defaults(subcmd='apply')

    git_am_parser = subparsers.add_parser(
        'git-am', parents=[hash_parser], conflict_handler='resolve',
        help="apply a patch to current git branch using 'git am'")
    git_am_parser.add_argument(
        '-s', '--signoff', action='store_true',
        help="pass '--signoff' to 'git-am'")
    git_am_parser.add_argument(
        '-3', '--3way', action='store_true',
        help="pass '--3way' to 'git-am'")
    git_am_parser.set_defaults(subcmd='git_am')

    get_parser = subparsers.add_parser(
        'get', parents=[hash_parser], conflict_handler='resolve',
        help="download a patch and save it locally"
    )
    get_parser.set_defaults(subcmd='get')

    info_parser = subparsers.add_parser(
        'info', parents=[hash_parser], conflict_handler='resolve',
        help="show information for a given patch ID")
    info_parser.set_defaults(subcmd='info')

    projects_parser = subparsers.add_parser(
        'projects',
        help="list all projects")
    projects_parser.set_defaults(subcmd='projects')

    check_list_parser = subparsers.add_parser(
        'check-list',
        add_help=False,
        help="list all checks"
    )
    check_list_parser.set_defaults(subcmd='check_list')

    check_info_parser = subparsers.add_parser(
        'check-info', add_help=False,
        help="show information for a given check")
    check_info_parser.add_argument(
        'check_id', metavar='ID', action='store', type=int,
        help="check ID")
    check_info_parser.set_defaults(subcmd='check_info')

    check_create_parser = subparsers.add_parser(
        'check-create', parents=[hash_parser], conflict_handler='resolve',
        help="add a check to a patch")
    check_create_parser.add_argument(
        '-c', metavar='CONTEXT')
    check_create_parser.add_argument(
        '-s', choices=('pending', 'success', 'warning', 'fail'))
    check_create_parser.add_argument(
        '-u', metavar='TARGET_URL', default="")
    check_create_parser.add_argument(
        '-d', metavar='DESCRIPTION', default="")
    check_create_parser.set_defaults(subcmd='check_create')

    states_parser = subparsers.add_parser(
        'states',
        help="show list of potential patch states")
    states_parser.set_defaults(subcmd='states')

    view_parser = subparsers.add_parser(
        'view', parents=[hash_parser], conflict_handler='resolve',
        help="view a patch")
    view_parser.set_defaults(subcmd='view')

    update_parser = subparsers.add_parser(
        'update', parents=[hash_parser], conflict_handler='resolve',
        help="update patch",
        epilog="using a COMMIT-REF allows for only one ID to be specified")
    update_parser.add_argument(
        '-c', metavar='COMMIT-REF',
        help="commit reference hash")
    update_parser.add_argument(
        '-s', metavar='STATE',
        help="set patch state (e.g., 'Accepted', 'Superseded' etc.)")
    update_parser.add_argument(
        '-a', choices=['yes', 'no'],
        help="set patch archived state")
    update_parser.set_defaults(subcmd='update')

    list_parser = subparsers.add_parser(
        'list', parents=[filter_parser],
        help='list patches using optional filters')
    list_parser.set_defaults(subcmd='list')

    # Poor man's argparse aliases: we register the "search" parser but
    # effectively use "list" for the help-text.
    search_parser = subparsers.add_parser(
        "search", parents=[filter_parser],
        help="alias for 'list'")
    search_parser.set_defaults(subcmd='list')

    if len(sys.argv) < 2:
        action_parser.print_help()
        sys.exit(0)

    args = action_parser.parse_args()
    args = dict(vars(args))
    action = args.get('subcmd')

    # set defaults
    filt = Filter()
    commit_str = None
    url = DEFAULT_URL

    use_hashes = args.get('hash')
    archived_str = args.get('a')
    state_str = args.get('s')
    project_str = args.get('p')
    submitter_str = args.get('w')
    delegate_str = args.get('d')
    format_str = args.get('f')
    patch_ids = args.get('id') or []
    msgid_str = args.get('m')
    commit_str = args.get('c')

    # update multiple IDs with a single commit-hash does not make sense
    if commit_str and len(patch_ids) > 1 and action == 'update':
        update_parser.error(
            "Declining update with COMMIT-REF on multiple IDs")

    if state_str is None and archived_str is None and action == 'update':
        update_parser.error(
            'Must specify one or more update options (-a or -s)')

    if args.get('n'):
        try:
            filt.add('max_count', args.get('n'))
        except:
            action_parser.error("Invalid maximum count '%s'" % args.get('n'))

    if args.get('N'):
        try:
            filt.add('max_count', 0 - args.get('N'))
        except:
            action_parser.error("Invalid maximum count '%s'" % args.get('N'))

    do_signoff = args.get('signoff')
    do_three_way = args.get('3way')

    # grab settings from config files
    config = ConfigParser.ConfigParser()
    config.read([CONFIG_FILE])

    if not config.has_section('options') and os.path.exists(CONFIG_FILE):
        sys.stderr.write('%s is in the old format. Migrating it...' %
                         CONFIG_FILE)

        old_project = config.get('base', 'project')

        new_config = ConfigParser.ConfigParser()
        new_config.add_section('options')

        new_config.set('options', 'default', old_project)
        new_config.add_section(old_project)

        new_config.set(old_project, 'url', config.get('base', 'url'))
        if config.has_option('auth', 'username'):
            new_config.set(
                old_project, 'username', config.get('auth', 'username'))
        if config.has_option('auth', 'password'):
            new_config.set(
                old_project, 'password', config.get('auth', 'password'))

        old_config_file = CONFIG_FILE + '.orig'
        shutil.copy2(CONFIG_FILE, old_config_file)

        with open(CONFIG_FILE, 'wb') as fd:
            new_config.write(fd)

        sys.stderr.write(' Done.\n')
        sys.stderr.write(
            'Your old %s was saved to %s\n' % (CONFIG_FILE, old_config_file))
        sys.stderr.write(
            'and was converted to the new format. You may want to\n')
        sys.stderr.write('inspect it before continuing.\n')
        sys.exit(1)

    if not project_str:
        try:
            project_str = config.get('options', 'default')
        except:
            sys.stderr.write(
                'No default project configured in %s\n' % CONFIG_FILE)
            sys.exit(1)

    if not config.has_section(project_str):
        sys.stderr.write(
            'No section for project %s in %s\n' % (CONFIG_FILE, project_str))
        sys.exit(1)
    if not config.has_option(project_str, 'url'):
        sys.stderr.write(
            'No URL for project %s in %s\n' % (CONFIG_FILE, project_str))
        sys.exit(1)

    if not do_signoff and config.has_option('options', 'signoff'):
        do_signoff = config.getboolean('options', 'signoff')
    if not do_signoff and config.has_option(project_str, 'signoff'):
        do_signoff = config.getboolean(project_str, 'signoff')
    if not do_three_way and config.has_option('options', '3way'):
        do_three_way = config.getboolean('options', '3way')
    if not do_three_way and config.has_option(project_str, '3way'):
        do_three_way = config.getboolean(project_str, '3way')

    url = config.get(project_str, 'url')

    transport = Transport(url)
    if action in auth_actions:
        if config.has_option(project_str, 'username') and \
                config.has_option(project_str, 'password'):
            transport.set_credentials(
                config.get(project_str, 'username'),
                config.get(project_str, 'password'))
        else:
            sys.stderr.write("The %s action requires authentication, but no "
                             "username or password\nis configured\n" % action)
            sys.exit(1)

    if project_str:
        filt.add("project", project_str)

    if state_str:
        filt.add("state", state_str)

    if archived_str:
        filt.add("archived", archived_str == 'yes')

    if msgid_str:
        filt.add("msgid", msgid_str)

    try:
        rpc = xmlrpclib.Server(url, transport=transport)
    except:
        sys.stderr.write("Unable to connect to %s\n" % url)
        sys.exit(1)

    if use_hashes:
        patch_ids = [
            patch_id_from_hash(rpc, project_str, x) for x in patch_ids]
    else:
        try:
            patch_ids = [int(x) for x in patch_ids]
        except ValueError:
            hash_parser.error('Patch IDs must be integers')

    if action == 'list' or action == 'search':
        if args.get('patch_name') is not None:
            filt.add("name__icontains", args.get('patch_name'))
        action_list(rpc, filt, submitter_str, delegate_str, format_str)

    elif action.startswith('project'):
        action_projects(rpc)

    elif action.startswith('state'):
        action_states(rpc)

    elif action == 'view':
        pager = os.environ.get('PAGER')
        if pager:
            pager = subprocess.Popen(
                pager.split(), stdin=subprocess.PIPE
            )
        if pager:
            i = list()
            for patch_id in patch_ids:
                s = rpc.patch_get_mbox(patch_id)
                if len(s) > 0:
                    i.append(s)
            if len(i) > 0:
                pager.communicate(input="\n".join(i).encode("utf-8"))
            pager.stdin.close()
        else:
            for patch_id in patch_ids:
                s = rpc.patch_get_mbox(patch_id)
                if len(s) > 0:
                    print(s)

    elif action == 'info':
        for patch_id in patch_ids:
            action_info(rpc, patch_id)

    elif action == 'get':
        for patch_id in patch_ids:
            action_get(rpc, patch_id)

    elif action == 'apply':
        for patch_id in patch_ids:
            ret = action_apply(rpc, patch_id)
            if ret:
                sys.stderr.write("Apply failed with exit status %d\n" % ret)
                sys.exit(1)

    elif action == 'git_am':
        cmd = ['git', 'am']
        if do_signoff:
            cmd.append('-s')
        if do_three_way:
            cmd.append('-3')
        for patch_id in patch_ids:
            ret = action_apply(rpc, patch_id, cmd)
            if ret:
                sys.stderr.write("'git am' failed with exit status %d\n" % ret)
                sys.exit(1)

    elif action == 'update':
        for patch_id in patch_ids:
            action_update_patch(
                rpc, patch_id, state=state_str, archived=archived_str,
                commit=commit_str)

    elif action == 'check_list':
        action_check_list(rpc)

    elif action == 'check_info':
        check_id = args['check_id']
        action_check_info(rpc, check_id)

    elif action == 'check_create':
        for patch_id in patch_ids:
            action_check_create(
                rpc, patch_id, args['c'], args['s'], args['u'], args['d'])

    else:
        sys.stderr.write("Unknown action '%s'\n" % action)
        action_parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        import traceback
        traceback.print_exc()
        sys.stderr.write('Try exporting the LANG or LC_ALL env vars. See '
                         'pwclient --help for more details.\n')
        sys.exit(1)
