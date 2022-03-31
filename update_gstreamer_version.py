"""
Simple script to update the manifest GStreamer modules version.

Run with:

    $ ./update_gstreamer_version.py /path/to/a/gst-build <TAG>

for example:

    $ ./update_gstreamer_version.py /home/thiblahute/devel/gstreamer/gst-build 1.14.0
"""

import os
import subprocess
import sys
import json

def expand_json_file(manifest, gst_repos_path, tag):
    """Creates the manifest file."""
    with open(manifest, "r") as tf:
        txt = tf.read()

    template = json.loads(txt)
    print("-> Generating %s against GStreamer %s" %(manifest, tag))

    gst_repos_path = os.path.abspath(gst_repos_path)
    for module in template["modules"]:
        if not isinstance(module, dict) or module["sources"][0]["type"] != "git":
            continue

        if not module["name"].startswith("gst"):
            continue

        repo = os.path.join(gst_repos_path, module["name"])
        if not os.path.exists(os.path.join(repo, ".git")):
            print("-> Module: %s NOT FOUND... can't update" % (
                  repo))
            continue

        commit = subprocess.check_output(
            ["git", "rev-list", "-n", "1", tag],
            cwd=repo).decode("utf-8").strip("\n")

        module["sources"][0]["tag"] = tag
        module["sources"][0]["commit"] = commit

    with open(manifest, "w") as of:
        print(json.dumps(template, indent=4), file=of)

if __name__ == "__main__":
    expand_json_file(sys.argv[1], sys.argv[2], sys.argv[3])

