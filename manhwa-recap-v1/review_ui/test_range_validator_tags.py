import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import range_compile as rc

def test_range_abbreviations_accepted():
    meta_fog = {'chapters_start': 38, 'chapters_end': 41}
    assert rc.problems('[38-41] A Genius DOCTOR Uses An OP SYSTEM To Survive Prison | Manhwa Recap', meta_fog) == []
    assert rc.problems('Ch. 38-41 A Genius DOCTOR', meta_fog) == []
    assert rc.problems('Chapter 38-41 A Genius DOCTOR', meta_fog) == []
    assert rc.problems('[Ch. 38-41] A Genius DOCTOR', meta_fog) == []

    meta_villain = {'chapters_start': 361, 'chapters_end': 362}
    assert rc.problems('OP VILLAIN Turns Stars Into Pawns In His Cosmic Game | I Am The Fated Villain Ch. 361-362', meta_villain) == []

    meta_death = {'chapters_start': 88, 'chapters_end': 91}
    assert rc.problems('[88-91] He Destroyed The World, But Regressed To Save It! | Manhwa Recap', meta_death) == []

    meta_mage = {'chapters_start': 104, 'chapters_end': 108}
    assert rc.problems('[104-108] LEGENDARY Dark Mage Gets Called To Military Conscription', meta_mage) == []

    meta_regressor = {'chapters_start': 32, 'chapters_end': 33}
    assert rc.problems('His Infinite REGRESSION - A Regressors Tale Of Cultivation Ch. 32-33', meta_regressor) == []
    assert len(rc.problems('His Infinite REGRESSION - A Regressors Tale Of Cultivation Ch. 32', meta_regressor)) > 0
    print('PASS: All range abbreviation checks verified!')

if __name__ == '__main__':
    test_range_abbreviations_accepted()
