# -*- coding: utf-8 -*-
'''
Various functions to be used by windows during start up and to monkey patch
missing functions in other modules
'''
from __future__ import absolute_import
import platform
import re
import ctypes

# Import Salt Libs
from salt.exceptions import CommandExecutionError

# Import 3rd Party Libs
try:
    import psutil
    import pywintypes
    import win32api
    import win32net
    import win32security
    from win32con import HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# Although utils are often directly imported, it is also possible to use the
# loader.
def __virtual__():
    '''
    Only load if Win32 Libraries are installed
    '''
    if not HAS_WIN32:
        return False, 'This utility requires pywin32'

    return 'win_functions'


def get_parent_pid():
    '''
    This is a monkey patch for os.getppid. Used in:
    - salt.utils.parsers

    Returns:
        int: The parent process id
    '''
    return psutil.Process().ppid()


def is_admin(name):
    '''
    Is the passed user a member of the Administrators group

    Args:
        name (str): The name to check

    Returns:
        bool: True if user is a member of the Administrators group, False
        otherwise
    '''
    groups = get_user_groups(name, True)

    for group in groups:
        if group in ('S-1-5-32-544', 'S-1-5-18'):
            return True

    return False


def get_user_groups(name, sid=False):
    '''
    Get the groups to which a user belongs

    Args:
        name (str): The user name to query
        sid (bool): True will return a list of SIDs, False will return a list of
        group names

    Returns:
        list: A list of group names or sids
    '''
    if name == 'SYSTEM':
        # 'win32net.NetUserGetLocalGroups' will fail if you pass in 'SYSTEM'.
        groups = [name]
    else:
        groups = win32net.NetUserGetLocalGroups(None, name)

    if not sid:
        return groups

    ret_groups = set()
    for group in groups:
        ret_groups.add(get_sid_from_name(group))

    return ret_groups


def get_sid_from_name(name):
    '''
    This is a tool for getting a sid from a name. The name can be any object.
    Usually a user or a group

    Args:
        name (str): The name of the user or group for which to get the sid

    Returns:
        str: The corresponding SID
    '''
    # If None is passed, use the Universal Well-known SID "Null SID"
    if name is None:
        name = 'NULL SID'

    try:
        sid = win32security.LookupAccountName(None, name)[0]
    except pywintypes.error as exc:
        raise CommandExecutionError(
            'User {0} not found: {1}'.format(name, exc.strerror))

    return win32security.ConvertSidToStringSid(sid)


def get_current_user(with_domain=True):
    '''
    Gets the user executing the process

    Returns:
        str: The user name
    '''
    try:
        user_name = win32api.GetUserNameEx(win32api.NameSamCompatible)
        if user_name[-1] == '$':
            # Make the system account easier to identify.
            # Fetch sid so as to handle other language than english
            test_user = win32api.GetUserName()
            if test_user == 'SYSTEM':
                user_name = 'SYSTEM'
            elif get_sid_from_name(test_user) == 'S-1-5-18':
                user_name = 'SYSTEM'
        elif not with_domain:
            user_name = win32api.GetUserName()
    except pywintypes.error as exc:
        raise CommandExecutionError(
            'Failed to get current user: {0}'.format(exc.strerror))

    if not user_name:
        return False

    return user_name


def get_sam_name(username):
    r'''
    Gets the SAM name for a user. It basically prefixes a username without a
    backslash with the computer name. If the user does not exist, a SAM
    compatible name will be returned using the local hostname as the domain.

    i.e. salt.utils.get_same_name('Administrator') would return 'DOMAIN.COM\Administrator'

    .. note:: Long computer names are truncated to 15 characters
    '''
    try:
        sid_obj = win32security.LookupAccountName(None, username)[0]
    except pywintypes.error:
        return '\\'.join([platform.node()[:15].upper(), username])
    username, domain, _ = win32security.LookupAccountSid(None, sid_obj)
    return '\\'.join([domain, username])


def escape_argument(arg, escape=True):
    '''
    Escape the argument for the cmd.exe shell.
    See http://blogs.msdn.com/b/twistylittlepassagesallalike/archive/2011/04/23/everyone-quotes-arguments-the-wrong-way.aspx

    First we escape the quote chars to produce a argument suitable for
    CommandLineToArgvW. We don't need to do this for simple arguments.

    Args:
        arg (str): a single command line argument to escape for the cmd.exe shell

    Kwargs:
        escape (bool): True will call the escape_for_cmd_exe() function
                       which escapes the characters '()%!^"<>&|'. False
                       will not call the function and only quotes the cmd

    Returns:
        str: an escaped string suitable to be passed as a program argument to the cmd.exe shell
    '''
    if not arg or re.search(r'(["\s])', arg):
        arg = '"' + arg.replace('"', r'\"') + '"'

    if not escape:
        return arg
    return escape_for_cmd_exe(arg)


def escape_for_cmd_exe(arg):
    '''
    Escape an argument string to be suitable to be passed to
    cmd.exe on Windows

    This method takes an argument that is expected to already be properly
    escaped for the receiving program to be properly parsed. This argument
    will be further escaped to pass the interpolation performed by cmd.exe
    unchanged.

    Any meta-characters will be escaped, removing the ability to e.g. use
    redirects or variables.

    Args:
        arg (str): a single command line argument to escape for cmd.exe

    Returns:
        str: an escaped string suitable to be passed as a program argument to cmd.exe
    '''
    meta_chars = '()%!^"<>&|'
    meta_re = re.compile('(' + '|'.join(re.escape(char) for char in list(meta_chars)) + ')')
    meta_map = {char: "^{0}".format(char) for char in meta_chars}

    def escape_meta_chars(m):
        char = m.group(1)
        return meta_map[char]

    return meta_re.sub(escape_meta_chars, arg)


def broadcast_setting_change(message='Environment'):
    '''
    Send a WM_SETTINGCHANGE Broadcast to all Windows

    Args:

        message (str):
            A string value representing the portion of the system that has been
            updated and needs to be refreshed. Default is ``Environment``. These
            are some common values:

            - "Environment" : to effect a change in the environment variables
            - "intl" : to effect a change in locale settings
            - "Policy" : to effect a change in Group Policy Settings
            - a leaf node in the registry
            - the name of a section in the ``Win.ini`` file

            See lParam within msdn docs for
            `WM_SETTINGCHANGE <https://msdn.microsoft.com/en-us/library/ms725497%28VS.85%29.aspx>`_
            for more information on Broadcasting Messages.

            See GWL_WNDPROC within msdn docs for
            `SetWindowLong <https://msdn.microsoft.com/en-us/library/windows/desktop/ms633591(v=vs.85).aspx>`_
            for information on how to retrieve those messages.

    .. note::
        This will only affect new processes that aren't launched by services. To
        apply changes to the path or registry to services, the host must be
        restarted. The ``salt-minion``, if running as a service, will not see
        changes to the environment until the system is restarted. Services
        inherit their environment from ``services.exe`` which does not respond
        to messaging events. See
        `MSDN Documentation <https://support.microsoft.com/en-us/help/821761/changes-that-you-make-to-environment-variables-do-not-affect-services>`_
        for more information.

    CLI Example:

    ... code-block:: python

        import salt.utils.win_functions
        salt.utils.win_functions.broadcast_setting_change('Environment')
    '''
    # Listen for messages sent by this would involve working with the
    # SetWindowLong function. This can be accessed via win32gui or through
    # ctypes. You can find examples on how to do this by searching for
    # `Accessing WGL_WNDPROC` on the internet. Here are some examples of how
    # this might work:
    #
    # # using win32gui
    # import win32con
    # import win32gui
    # old_function = win32gui.SetWindowLong(window_handle, win32con.GWL_WNDPROC, new_function)
    #
    # # using ctypes
    # import ctypes
    # import win32con
    # from ctypes import c_long, c_int
    # user32 = ctypes.WinDLL('user32', use_last_error=True)
    # WndProcType = ctypes.WINFUNCTYPE(c_int, c_long, c_int, c_int)
    # new_function = WndProcType
    # old_function = user32.SetWindowLongW(window_handle, win32con.GWL_WNDPROC, new_function)
    broadcast_message = ctypes.create_unicode_buffer(message)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    result = user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0,
                                        broadcast_message, SMTO_ABORTIFHUNG,
                                        5000, 0)
    return result == 1