import nuke
import nukescripts
import re
import os
import itertools
import glob

from Qt import QtWidgets, QtCore

from utilities import cui


class ReplaceCameraException(Exception):
    pass


def getBackdrops(nodes=None):
    '''Get all the backdrops from selection or a list of nodes'''
    if nodes is None:
        nodes = nuke.selectedNodes()
    backdropNodes = set()
    for node in nodes:
        new_bd = nuke.getBackdrop(node)
        if new_bd:
            backdropNodes.add(new_bd)
    return backdropNodes


def iterNodes(stuff):
    '''Create an iterator for one or more nodes'''
    if isinstance(stuff, nuke.Node):
        yield stuff
    elif hasattr(stuff, '__iter__'):
        for thing in stuff:
            for node in iterNodes(thing):
                yield node


def restore_selection(add_new=True):
    '''Record selection before calling the function and try to restore it after
    execution'''
    def _decorator(func):
        def _restorer(*args, **kwargs):
            selection = nuke.selectedNodes()
            new_stuff = []
            try:
                new_stuff = func(*args, **kwargs)
            finally:
                nukescripts.clear_selection_recursive()
                if add_new:
                    for new_node in iterNodes(new_stuff):
                        try:
                            new_node.setSelected(True)
                        except AttributeError:
                            pass
                for node in selection:
                    try:
                        node.setSelected(True)
                    except ValueError:
                        pass
                return new_stuff

        return _restorer

    return _decorator


def findCameraUpstream(node, delete_visited=True):
    '''Backward breadth first search for camera node'''
    nodes = [node]
    while nodes:
        for node in nodes[:]:
            nodes.remove(node)
            try:
                if node.Class() == 'Camera':
                    return node
            except (ValueError, AttributeError):
                continue
            nodes.extend(node.dependencies())
            if delete_visited:
                nuke.delete(node)


def getOutputs(node):
    '''Get output nodes with their connect input index'''
    dependents = node.dependent()
    outputs = []
    for dep in dependents:
        for inp in range(dep.maxInputs()):
            if dep.input(inp) == node:
                outputs.append((dep, inp))
    return outputs


@restore_selection()
def replaceCamera(camera, path):
    '''Replace camera with one found in the path'''
    nukescripts.clear_selection_recursive()
    dependent = getOutputs(camera)
    x = camera.xpos()
    y = camera.ypos()
    new_node = nuke.nodePaste(path)
    if new_node is None:
        return None
    new_cam = findCameraUpstream(new_node)
    if new_cam is None:
        return None
    new_cam.setXYpos(x, y)
    for dep in new_cam.dependent():
        nuke.delete(dep)
    nuke.delete(camera)
    for dep, i in dependent:
        dep.setInput(i, new_cam)
    return new_cam


class BackdropShot(object):
    shot_re = re.compile(r'SH(\d+)([a-z]?)', re.IGNORECASE)
    sequence_re = re.compile(r'SQ(\d+)([a-z]?)', re.IGNORECASE)
    episode_re = re.compile(r'EP(\d+)([a-z]?)', re.IGNORECASE)
    episode_re2 = re.compile(r'02_production[\\\/]+([^\\\/]*)')
    project_re = re.compile('(?<=[\\/])[^\\/]*(?=[\\/]02_production)')
    char_re = re.compile('char', re.IGNORECASE)
    beauty_re = re.compile('beauty', re.IGNORECASE)
    bases = ['L:',
             os.path.join('P:', 'external'),
             os.path.join('P:', 'external', '*'),
             os.path.join('L:', '*')]
    camera_templates = [
            os.path.join(
                '%(base)s', '%(project)s', '02_production', '%(episode)s',
                'SEQUENCES', '%(sequence)s', 'SHOTS', '%(shot)s', 'animation',
                'camera', '%(cam)s'),
            os.path.join(
                '%(base)s', '%(project)s', '02_production', '%(episode)s',
                '%(shot)s', 'animation', 'camera', '%(cam)s'),
            os.path.join(
                '%(base)s', '%(project)s', '02_production', '%(episode)s',
                '%(sequence)s', '%(shot)s', 'animation', 'camera', '%(cam)s'),
            os.path.join(
                '%(base)s', '%(project)s', '%(episode)s', '%(sequence)s',
                '%(shot)s', 'animation', 'camera', '%(cam)s')]
    project_maps = {
            'Suntop': {'proj_folder': 'Suntop_Season_01'},
            'Suntop_S04': {'proj_folder': 'Suntop_Season_04'}}

    def __init__(self,
                 episode=None,
                 sequence=None,
                 shot=None,
                 project=None,
                 backdrop=None):
        self.episode = episode
        self.sequence = sequence
        self.shot = shot
        self.project = project
        self.backdrop = backdrop

    def __str__(self):
        return ('BackdropShot(project=%s, episode=%s, sequence=%s, shot=%s,'
                ' backdrop=%r)' % (self.project, self.episode, self.sequence,
                                   self.shot, self.backdrop))

    __repr__ = __str__

    def _getCameraPaths_exact_method(self):
        '''Construct the path for location where the maya camera exported from
        animation maybe found'''
        templates = self.camera_templates
        bases = self.bases

        projects = [self.project]
        mapped = self.project_maps.get(self.project, {}).get('proj_folder')
        if mapped:
            projects.append(mapped)

        episodes = [self.episode]
        ep_no, ep_let = self.getEpisodeNumber()
        if ep_no:
            episodes.extend([
                'ep%02d%s' % (ep_no, ep_let),
                'ep%03d%s' % (ep_no, ep_let)])

        sequences = ['sq%03d%s' % self.getSequenceNumber()]
        sequences.extend([
            '%s_%s' % (ep, seq)
            for ep, seq in itertools.product(episodes, sequences)])

        shots = ['sh%03d%s' % self.getShotNumber()]
        shots.extend([
            '%s_%s' % (sq, sh)
            for sq, sh in itertools.product(sequences, shots)
            ])

        cams = [pat % sh
                for pat, sh in itertools.product(
                    ['%s_cam.nk', '%s.nk'], shots)]

        paths = []
        for temp, base, proj, ep, sq, sh, cam in itertools.product(
                templates, bases, projects, episodes, sequences, shots, cams):
            path = temp % {
                    'base': base, 'project': proj, 'episode': ep,
                    'sequence': sq, 'shot': sh, 'cam': cam}
            if os.path.isfile(path):
                paths.append(path)

        return paths

    def _getCameraPaths_blob_method(self,
                                    multi_episode=False,
                                    multi_sequence=False):
        templates = self.camera_templates
        bases = self.bases

        projects = [self.project]
        mapped = self.project_maps.get(self.project, {}).get('proj_folder')
        if mapped:
            projects.append(mapped)

        if multi_episode:
            episodes = ['*']
        else:
            episodes = [self.episode]
            ep_no, ep_let = self.getEpisodeNumber()
            if ep_no:
                episodes = ['ep%02d%s' % (ep_no, ep_let),
                            'ep%03d%s' % (ep_no, ep_let)]

        if multi_sequence:
            sequences = ['*']
        else:
            sequences = ['*sq%03d%s' % self.getSequenceNumber()]
        shots = ['*sh%03d%s' % self.getShotNumber()]
        cams = ['*.nk']

        paths = []
        for temp, base, proj, ep, sq, sh, cam in itertools.product(
                templates, bases, projects, episodes, sequences, shots, cams):
            path = temp % {'base': base, 'project': proj, 'episode': ep,
                           'sequence': sq, 'shot': sh, 'cam': cam}
            globs = glob.glob(path)
            paths.extend(globs)

        return paths

    getCameraPaths = _getCameraPaths_blob_method

    def getCameras(self):
        '''Get Cameras found in the backdrop'''
        cameras = []
        if self.backdrop:
            for node in nuke.getBackdropNodes(self.backdrop):
                if node.Class() == 'Camera':
                    cameras.append(node)
        return cameras

    def replaceCameras(self, path=None):
        '''Replace Cameras found in the Backdrop'''
        cameras = self.getCameras()
        if not cameras:
            return

        if path is None:
            paths = self.getCameraPaths()
            if not paths:
                paths = self.getCameraPaths(multi_episode=True)

            if len(paths) == 1:
                path = paths[0]
            elif len(paths) > 1:
                path = self.getPathChoice(paths)

        if path is None:
            raise ReplaceCameraException(
                    'Cannot find camera on path for backdrop %r' % self)

        for cam in self.getCameras():
            try:
                replaceCamera(cam, path)
            except ReplaceCameraException:
                import traceback
                traceback.print_exc()

    def getNumber(self, element='episode'):
        '''Parsing episode, sequence and shots for numbers'''
        value = None
        exp = None
        try:
            value = getattr(self, element)
            exp = getattr(self, '_'.join([element, 're']))
        except AttributeError:
            pass
        if value is not None and exp is not None:
            match = exp.match(value)
            if match:
                return int(match.group(1)), match.group(2)
        return None, None

    def getPathChoice(self, paths):
        dlg = QtWidgets.QDialog(QtWidgets.QApplication.activeWindow())
        dlg.setWindowTitle('Replace Camera')
        dlg.setMinimumWidth(600)
        dlg.setMinimumHeight(100)

        edit = QtWidgets.QTextEdit()
        text = "<p>Multiple Paths found for the following Backdrop</p>"
        text += "<table style=\"margin: auto\">"
        text += "<tr><td><b>Project:</b></td><td>%s</td></tr>" % self.project
        text += "<tr><td><b>Episode:</b></td><td>%s</td></tr>" % self.episode
        text += "<tr><td><b>Sequence:</b></td><td>%s</td></tr>" % self.sequence
        text += "<tr><td><b>Shot:</b></td><td>%s</td></tr>" % self.shot
        text += ("<tr><td><b>Backdrop Node:</b>"
                 "</td><td>%s</td></tr>" % self.backdrop.name())
        text += "</table>"
        edit.setText(text)
        edit.setEnabled(False)
        label = QtWidgets.QLabel('Select Cam Path:')
        box = QtWidgets.QComboBox(dlg)
        box.addItems(paths)
        okbtn = QtWidgets.QPushButton('OK')
        cancelbtn = QtWidgets.QPushButton('Cancel')

        hlayout = QtWidgets.QHBoxLayout()
        hlayout.addWidget(label)
        hlayout.addWidget(box)
        hlayout.addWidget(okbtn)
        hlayout.addWidget(cancelbtn)

        vlayout = QtWidgets.QVBoxLayout(dlg)
        vlayout.addWidget(edit)
        vlayout.addLayout(hlayout)

        okbtn.clicked.connect(dlg.accept)
        cancelbtn.clicked.connect(dlg.reject)

        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            return paths[box.currentIndex()]

    def getShotNumber(self):
        return self.getNumber('shot')

    def getEpisodeNumber(self):
        return self.getNumber('episode')

    def getSequenceNumber(self):
        return self.getNumber('sequence')

    @classmethod
    def getFromPath(cls, path):
        '''Parse for project, episode, sequence and shot given a path'''
        project = shot = sequence = episode = None
        shot_match = cls.shot_re.search(path)
        sequence_match = cls.sequence_re.search(path)
        episode = None
        episode_match = cls.episode_re.search(path)
        if not episode_match:
            episode_match = cls.episode_re2.search(path)
            if episode_match:
                episode = episode_match.group(1)
        else:
            episode = episode_match.group()
        project_match = cls.project_re.search(path)
        if sequence_match:
            sequence = sequence_match.group()
        if shot_match:
            shot = shot_match.group()
        if project_match:
            project = project_match.group()
        if episode and sequence and shot and project:
            return BackdropShot(
                episode=episode, sequence=sequence, shot=shot, project=project)

    @classmethod
    def getPathScore(cls, path):
        '''Get a path score, higher score means more likelihood for being the
        representative path in a backdrop'''
        score = 0
        for attr in dir(cls):
            if attr.endswith('_re'):
                exp = getattr(cls, attr)
                for f in exp.findall(path):
                    score += 10
        return score

    @classmethod
    def getPathsFromBackdrop(cls, backdrop):
        '''Get all paths from the read nodes within the backdrop ordered by
        the likelihood of their relevance to shot being composited in the
        backdrop'''
        paths = dict()
        for node in nuke.getBackdropNodes(backdrop):
            if node.Class() == 'Read':
                file_path = node.knob('file').getValue()
                if file_path:
                    paths[file_path] = cls.getPathScore(file_path)
        return sorted(paths, key=paths.get, reverse=True)

    @classmethod
    def getFromBackdrop(cls, backdrop):
        '''Get BackdropShot object from the backdrop'''
        for node in nuke.getBackdropNodes(backdrop):
            file_paths = cls.getPathsFromBackdrop(backdrop)
            if file_paths:
                shot = cls.getFromPath(file_paths[0])
                if shot:
                    shot.backdrop = backdrop
                    return shot

    @classmethod
    def getFromNodes(cls, nodes=None):
        '''Given a list or a selection of nodes get relevent BackdropShot
        objects per backdrop'''
        if nodes is None:
            nodes = nuke.selectedNodes()
        backdrops = list(getBackdrops(nodes))
        shots = []
        for bd in backdrops:
            shot = cls.getFromBackdrop(bd)
            if shot:
                shots.append(shot)
        return shots

    def showMissingCameraError(self):
        cui.showMessage(QtWidgets.QApplication.activeWindow(),
                        title='Replace Camera',
                        msg='No camera file found on %r' % self,
                        icons=QtWidgets.QMessageBox.Critical)


@restore_selection()
def replaceBackdropCameras(nodes=None):
    '''Given a list or selection of nodes replace all the camera nodes using
    paths detected from read nodes within containing backdrops'''
    results = []
    if nodes is None:
        nodes = nuke.selectedNodes()
    backdropShots = BackdropShot.getFromNodes(nodes)

    for bds in backdropShots:
        cameras = bds.getCameras()
        if not cameras:
            continue

        path = None

        paths = bds.getCameraPaths()
        if not paths:
            paths = bds.getCameraPaths(multi_episode=True)

        if len(paths) == 1:
            path = paths[0]
        elif len(paths) > 1:
            try:
                path = bds.getPathChoice(paths)
            except Exception as e:
                import traceback
                traceback.print_exc()

        if not path:
            bds.showMissingCameraError()

        try:
            for cam in cameras:
                replacement_cam = replaceCamera(cam, path)
                if replacement_cam:
                    results.append((bds.backdrop, path, replacement_cam))

        except ReplaceCameraException:
            import traceback
            traceback.print_exc()

    return results


def main():
    cams = replaceBackdropCameras()
    if cams:
        print "%d cameras substituted!" % len(cams)
    else:
        print "No cameras substituted"
    return cams


if __name__ == "__main__":
    main()
