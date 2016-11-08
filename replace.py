import nuke
import nukescripts
import re

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

def findCameraUpstream(node, delete_visited=True):
    '''Backward breadth first search for camera node'''
    nodes = [node]
    while nodes:
        for node in nodes[:]:
            nodes.remove(node)
            if node is None:
                continue
            if node.Class() == 'Camera':
                return node
            nodes.extend(node.dependencies())
            if delete_visited:
                nuke.delete(node)

def getOutputs(node):
    '''Get output nodes with their connect input index'''
    dependents = node.dependent()
    outputs = []
    for dep in dependents:
        for inp in range( dep.maxInputs() ):
            if dep.input(inp) == node:
                outputs.append((dep, inp))
    return outputs

def replaceCamera(camera, path):
    '''Replace camera with one found in the path'''
    nukescripts.clear_selection_recursive()
    dependent = getOutputs(camera)
    x = camera.xpos()
    y = camera.ypos()
    new_node = nuke.nodePaste(path)
    new_cam = findCameraUpstream(new_node)
    if new_cam is None:
        raise ReplaceCameraException, 'Camera node not found in %s' % path
    new_cam.setXYpos(x, y)
    for dep in new_cam.dependent():
        nuke.delete(dep)
    nuke.delete(camera)
    for dep, i in dependent:
        dep.setInput(i, new_cam)
    return new_cam

class BackdropShot(object):
    shot_re = re.compile('SH(\d+)', re.IGNORECASE)
    sequence_re = re.compile('SQ(\d+)', re.IGNORECASE)
    episode_re = re.compile('EP(\d+)', re.IGNORECASE)
    project_re = re.compile('(?<=[\\/])[^\\/]*(?=[\\/]02_production)')
    char_re = re.compile('char', re.IGNORECASE)
    beauty_re = re.compile('beauty', re.IGNORECASE)
    camera_template = (
            "P:\\external\\%(project)s\\02_production\\%(episode)s"
            "\\SEQUENCES\\%(sequence)s\\SHOTS\\%(sequence)s_%(shot)s"
            "\\animation\\camera\\%(episode)s_%(sequence)s_%(shot)s.nk"
                    )
    def __init__(self, episode=None, sequence=None, shot=None, project=None, backdrop=None):
        self.episode = episode
        self.sequence = sequence
        self.shot = shot
        self.project = project
        self.backdrop = backdrop

    def __str__(self):
        return ( 'BackdropShot(project=%s, episode=%s, sequence=%s, shot=%s,'
                 ' backdrop=%r)' % ( self.project, self.episode, self.sequence,
                    self.shot, self.backdrop) )

    __repr__ = __str__

    def getCameraPath(self):
        '''Construct the path for location where the maya camera exported from
        animation maybe found'''
        mydict = { 'project': self.project,
            'episode': 'ep%02d'%self.getEpisodeNumber(),
            'sequence': 'sq%03d'%self.getSequenceNumber(),
            'shot': 'sh%03d'%self.getShotNumber() }
        return self.camera_template % mydict

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
        if path is None:
            path = self.getCameraPath()
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
                return int(match.group(1))

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
        episode_match = cls.episode_re.search(path)
        project_match = cls.project_re.search(path)
        if episode_match:
            episode = episode_match.group()
        if sequence_match:
            sequence = sequence_match.group()
        if shot_match:
            shot = shot_match.group()
        if project_match:
            project = project_match.group()
        if episode and sequence and shot and project:
            return BackdropShot(episode=episode, sequence=sequence, shot=shot,
                    project=project)


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

def replaceBackdropCameras(nodes = None):
    '''Given a list or selection of nodes replace all the camera nodes using
    paths detected from read nodes within containing backdrops'''
    results = []
    if nodes is None:
        nodes = nuke.selectedNodes()
    backdropShots = BackdropShot.getFromNodes(nodes)
    for bds in backdropShots:
        cameras = bds.getCameras()
        for cam in cameras:
            path = bds.getCameraPath()
            replacement_cam = replaceCamera(cam, path)
            if replacement_cam:
                results.append((bds.backdrop, path, replacement_cam))
    nukescripts.clear_selection_recursive()
    for res in results:
        res[2].setSelected(True)
    return results

def main():
    print replaceBackdropCameras()

if __name__ == "__main__":
    main()
