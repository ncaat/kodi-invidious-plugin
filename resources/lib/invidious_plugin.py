from datetime import datetime

import requests
import sys
from urllib.parse import urlencode
from urllib.parse import parse_qs

import requests
import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin

import inputstreamhelper

import invidious_api


class InvidiousPlugin:
    # special lists provided by the Invidious API
    SPECIAL_LISTS = ("trending", "popular")

    INSTANCESURL = "https://api.invidious.io/instances.json?sort_by=type,health"
    def __init__(self, base_url, addon_handle, args):
        self.base_url = base_url
        self.addon_handle = addon_handle
        self.addon = xbmcaddon.Addon()
        self.args = args

        instance_url = xbmcplugin.getSetting(self.addon_handle, "instance_url")
        if 'auto' == instance_url:
            instance_url = self.instance_autodetect()
        xbmc.log(f'invidous using instance {instance_url}.', xbmc.LOGINFO)
        self.api_client = invidious_api.InvidiousAPIClient(instance_url)

    def instance_autodetect(self):
        xbmc.log('invidious picking instance automatically.', xbmc.LOGINFO)

        response = requests.get(self.INSTANCESURL, timeout=5)
        data = response.json()
        for instanceinfo in data:
            xbmc.log('invidious considering instance ' + str(instanceinfo), xbmc.LOGDEBUG)
            instancename, instance = instanceinfo
            if 'https' == instance['type']:
                instance_url = instance['uri']
                # Make sure the instance work for us.  This test avoid
                # those rejecting us with HTTP status 429.
                api_client = invidious_api.InvidiousAPIClient(instance_url)
                if api_client.fetch_special_list(self.SPECIAL_LISTS[0]):
                    return instance_url

        xbmc.log('invidious no working https type instance returned from api.invidious.io.', xbmc.LOGWARNING)
        # FIXME figure out how to show failing autodetection to the user.
        dialog = xbmcgui.Dialog()
        dialog.notification(
            'No working instance URL found',
            'No working https type instance returned from api.invidious.io.'
            "error"
        )
        raise ValueError("unable to find working Invidious instance")

    def build_url(self, action, **kwargs):
        if not action:
            raise ValueError("you need to specify an action")

        kwargs["action"] = action

        return self.base_url + "?" + urlencode(kwargs)

    def add_directory_item(self, *args, **kwargs):
        xbmcplugin.addDirectoryItem(self.addon_handle, *args, **kwargs)

    def end_of_directory(self):
        xbmcplugin.endOfDirectory(self.addon_handle)

    def display_search_results(self, results):
        # extracted from display_search
        for result in results:
            list_item = xbmcgui.ListItem(result.heading)

            list_item.setArt({
                "thumb": result.thumbnail_url,
            })

            datestr = datetime.utcfromtimestamp(result.published).date().isoformat()

            list_item.setInfo("video", {
                "title": result.heading,
                "mediatype": "video",
                "plot": result.description,
                "credits": result.author,
                "date": datestr,
                "dateadded": datestr,
                "duration": result.duration
            })

            # if this is NOT set, the plugin is called with an invalid handle when trying to play this item
            # seriously, Kodi? come on...
            # https://forum.kodi.tv/showthread.php?tid=173986&pid=1519987#pid1519987
            list_item.setProperty("IsPlayable", "true")

            url = self.build_url("play_video", video_id=result.id)

            self.add_directory_item(url=url, listitem=list_item)

        self.end_of_directory()

    def display_search(self):
        # query search terms with a dialog
        dialog = xbmcgui.Dialog()
        search_input = dialog.input(self.addon.getLocalizedString(30001), type=xbmcgui.INPUT_ALPHANUM)

        if len(search_input) == 0:
            return
        xbmc.log(f"invidious searching for {search_input}.", xbmc.LOGDEBUG)
        # search for the terms on Invidious
        results = self.api_client.search(search_input)

        # assemble menu with the results
        self.display_search_results(results)

    def display_special_list(self, special_list_name):
        if special_list_name not in self.__class__.SPECIAL_LISTS:
            raise ValueError(str(special_list_name) + " is not a valid special list")

        videos = self.api_client.fetch_special_list(special_list_name)

        self.display_search_results(videos)

    def display_channel_list(self, channel_id):
        # TODO: pagination
        videos = self.api_client.fetch_channel_list(channel_id)

        self.display_search_results(videos)

    def play_video(self, id):
        # TODO: add support for adaptive streaming
        video_info = self.api_client.fetch_video_information(id)

        xbmc.log(f"invidious playing video {video_info}.", xbmc.LOGDEBUG)

        listitem = None
        # check if playback via MPEG-DASH is possible
        if "dashUrl" in video_info:
            is_helper = inputstreamhelper.Helper("mpd")
            
            if is_helper.check_inputstream():
                url = video_info["dashUrl"]
                xbmc.log(f"invidious using mpeg-dash stream {url}.", xbmc.LOGDEBUG)
                listitem = xbmcgui.ListItem(path=url)
                listitem.setProperty("inputstream", is_helper.inputstream_addon)
                listitem.setProperty("inputstream.adaptive.manifest_type", "mpd")
            else:
                xbmc.log("invidious mpeg-dash input helper not available.", xbmc.LOGDEBUG)

        # as a fallback, we use the last oldschool stream, as it is
        # often the best quality.
        if listitem is None:
            url = video_info["formatStreams"][-1]["url"]
            xbmc.log("invidious playback failing back to non-dash stream {url}!", xbmc.LOGINFO)
            # it's pretty complicated to play a video by its URL in Kodi...
            listitem = xbmcgui.ListItem(path=url)

        datestr = datetime.utcfromtimestamp(video_info["published"]).date().isoformat()
        listitem.setInfo('video', {
                "title": video_info["title"],
                "mediatype": "video",
                "plot": video_info["description"],
                "credits": video_info["author"],
                "date": datestr,
                "dateadded": datestr,
                "duration": str(video_info["lengthSeconds"])
        })
        xbmcplugin.setResolvedUrl(self.addon_handle, succeeded=True, listitem=listitem)

    def display_main_menu(self):
        def add_list_item(label, path):
            listitem = xbmcgui.ListItem(label, path=path, )
            self.add_directory_item(url=self.build_url(path), listitem=listitem, isFolder=True)

        # video search item
        add_list_item(self.addon.getLocalizedString(30002), "search_video")

        for special_list_name in self.__class__.SPECIAL_LISTS:
            label = special_list_name[0].upper() + special_list_name[1:]
            add_list_item(label, special_list_name)

        self.end_of_directory()

    def run(self):
        """
        Web application style method dispatching.
        Uses querystring only, which is pretty oldschool CGI-like stuff.
        """

        action = self.args.get("action", [None])[0]

        # debugging
        xbmc.log("invidous --------------------------------------------", xbmc.LOGDEBUG)
        xbmc.log("invidous base url:" + str(self.base_url), xbmc.LOGDEBUG)
        xbmc.log("invidous handle:" + str(self.addon_handle), xbmc.LOGDEBUG)
        xbmc.log("invidous args:" + str(self.args), xbmc.LOGDEBUG)
        xbmc.log("invidous action:" + str(action), xbmc.LOGDEBUG)
        xbmc.log("invidous --------------------------------------------", xbmc.LOGDEBUG)

        # for the sake of simplicity, we just handle HTTP request errors here centrally
        try:
            if not action:
                self.display_main_menu()

            elif action == "search_video":
                self.display_search()

            elif action == "play_video":
                self.play_video(self.args["video_id"][0])

            elif action == "view_channel":
                self.display_channel_list(self.args["channel_id"][0])

            elif action in self.__class__.SPECIAL_LISTS:
                special_list_name = action
                self.display_special_list(special_list_name)

            else:
                raise RuntimeError("unknown action " + action)

        except requests.HTTPError as e:
            xbmc.log(f'invidous HTTP status {e.response.status_code} during action processing: {e.response.reason}', xbmc.LOGWARNING)
            dialog = xbmcgui.Dialog()
            dialog.notification(
                self.addon.getLocalizedString(30003),
                self.addon.getLocalizedString(30004) + str(e.response.status_code),
                "error"
            )

        except requests.Timeout:
            xbmc.log('invidous HTTP timed out during action processing', xbmc.LOGWARNING)
            dialog = xbmcgui.Dialog()
            dialog.notification(
                self.addon.getLocalizedString(30005),
                self.addon.getLocalizedString(30006),
                "error"
            )

    @classmethod
    def from_argv(cls):
        base_url = sys.argv[0]
        addon_handle = int(sys.argv[1])
        args = parse_qs(sys.argv[2][1:])

        return cls(base_url, addon_handle, args)
