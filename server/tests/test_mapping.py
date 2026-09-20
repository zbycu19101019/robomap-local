from app.mapping import MappingSurvey


def loc(x=0, y=0, source='manual', confidence=.9):
    return {'pose': {'x': x, 'y': y, 'heading': 0}, 'calibrated': True,
            'source': source, 'confidence': confidence}


def test_recording_and_persistence(tmp_path):
    s=MappingSurvey(tmp_path)
    assert not s.contact(loc())
    s.active=True
    assert s.contact(loc())
    assert not s.contact(loc())
    assert abs(s.points[0]['y']+.17)<.001
    restored=MappingSurvey(tmp_path)
    assert len(restored.points)==1
    assert not restored.active


def test_unobserved_autonomous_motion_is_not_a_wall(tmp_path):
    s=MappingSurvey(tmp_path);s.active=True
    assert not s.contact(loc(source='autonomous-last-known'))
    assert not s.contact(loc(confidence=.1))
    assert not s.points
    assert s.unlocated==2


def test_wall_fit_requires_evidence_and_does_not_bridge_rooms(tmp_path):
    s=MappingSurvey(tmp_path);s.active=True
    for x in (0,.25,.5,4,4.25,4.5):
        s.contact(loc(x=x))
    walls=s.walls()
    assert len(walls)==2
    assert all(abs(w['end']['x']-w['start']['x'])<.6 for w in walls)
    assert len({w['id'] for w in walls})==2


def test_scattered_points_are_not_a_wall(tmp_path):
    s=MappingSurvey(tmp_path);s.active=True
    for x,y in ((0,0),(.4,0),(.2,.4)):
        s.contact(loc(x,y))
    assert s.walls()==[]
