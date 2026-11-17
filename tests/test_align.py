"""Regression checks for packaged Skill; lives outside the 3-file Skill."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/align.py'
spec=importlib.util.spec_from_file_location('aligner', SCRIPT)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)

def source(*paras):
    k=0; out=[]
    for para in paras:
        arr=[]
        for s in para:
            k+=1; arr.append({'sid':f's{k:03d}', 'text':s})
        out.append({'paragraph':arr})
    return {'filetype':'passage','paragraphs':out}

def whisper(*w):
    return {'word_segments':[{'word':x[0],'start':x[1],'end':x[2]} for x in w]}

def convert(src,aud):
    title,paras,tokens=module.parse_source(src)
    ats,notices=module.parse_audio(aud)
    match,missing,extra=module.align(tokens,ats)
    return module.prepare_output(title,paras,tokens,ats,match,missing,extra,notices)

class AlignerTests(unittest.TestCase):
    def test_01_exact(self):
        out,rep=convert(source(['Hello world.']),whisper(('Hello',1,1.5),('world.',1.6,1.9)))
        self.assertEqual(out['paragraphs'][0][0]['timed_units'],[['Hello',1,1.5],['world',1.6,1.9]])
        self.assertEqual(rep['status'],'passed')
    def test_02_contraction_it_is(self):
        out,rep=convert(source(['before it is too late']),whisper(('before',1,2),("it's",2.1,2.5),('too',2.6,3),('late',3.1,3.5)))
        self.assertEqual(out['paragraphs'][0][0]['timed_units'][1], ['it is',2.1,2.5])
        self.assertEqual(rep['status'],'passed')
    def test_03_contraction_do_not(self):
        out,rep=convert(source(['I do not know.']),whisper(('I',1,1.2),("don't",1.3,1.6),('know.',1.7,2)))
        self.assertEqual(out['paragraphs'][0][0]['timed_units'][1], ['do not',1.3,1.6])
    def test_04_one_source_token_multiple_audio_tokens(self):
        out,rep=convert(source(['A mirror-symmetrical figure.']),whisper(('A',1,1.1),('mirror',1.2,1.5),('symmetrical',1.6,2),('figure',2.1,2.3)))
        self.assertEqual(out['paragraphs'][0][0]['timed_units'][1],['mirror-symmetrical',1.2,2])
        self.assertEqual(rep['status'],'passed')
    def test_05_spelling_variant(self):
        out,rep=convert(source(['They were synthesising cells.']),whisper(('They',1,1.2),('were',1.3,1.5),('synthesizing',1.6,1.9),('cells',2,2.3)))
        self.assertEqual(out['paragraphs'][0][0]['timed_units'][2],['synthesising',1.6,1.9])
        self.assertEqual(rep['status'],'passed')
    def test_06_audio_preamble_preserved_timestamp(self):
        out,rep=convert(source(['Hello world']),whisper(('Intro',1,2),('Hello',10,10.5),('world',11,11.5)))
        self.assertEqual(out['paragraphs'][0][0]['timed_units'][0],['Hello',10,10.5])
        self.assertEqual(rep['ignored_audio'][0]['position'],'before_source')
    def test_07_audio_extra_within_sentence(self):
        out,rep=convert(source(['We love science']),whisper(('We',1,1.2),('love',2,2.2),('really',2.3,2.5),('science',3,3.3)))
        self.assertEqual(rep['status'],'needs_review')
        self.assertEqual(out['paragraphs'][0][0]['text'],'We love science')
        self.assertEqual(rep['ignored_audio'][0]['position'],'within_source')
    def test_08_missing_source_word(self):
        out,rep=convert(source(['We absolutely love science']),whisper(('We',1,1.2),('love',2,2.2),('science',3,3.3)))
        self.assertEqual(rep['status'],'needs_review')
        self.assertIn(['absolutely',None,None], out['paragraphs'][0][0]['timed_units'])
    def test_09_source_text_preserved_case_punctuation(self):
        x='  “Hello,” said Mary—quietly.'
        out,rep=convert(source([x]),whisper(('Hello',1,1.2),('said',1.3,1.5),('Mary',1.6,1.9),('quietly',2,2.5)))
        self.assertEqual(out['paragraphs'][0][0]['text'],x)
        self.assertEqual(rep['status'],'passed')
    def test_10_no_cross_sentence_group(self):
        src=source(['It.','Is good.'])
        out,rep=convert(src,whisper(("it's",1,1.3),('good',1.4,1.7)))
        self.assertEqual(out['paragraphs'][0][0]['text'],'It.')
        self.assertEqual(out['paragraphs'][0][1]['text'],'Is good.')
        self.assertEqual(rep['status'],'needs_review')
    def test_11_duplicate_sid_rejected(self):
        x=source(['A.','B.']); x['paragraphs'][0]['paragraph'][1]['sid']='s001'
        with self.assertRaises(ValueError): module.parse_source(x)
    def test_12_missing_audio_times_null(self):
        out,rep=convert(source(['We play ball']),whisper(('We',1,1.2),('play',None,None),('ball',2,2.3)))
        self.assertIn(['play',None,None],out['paragraphs'][0][0]['timed_units'])
        self.assertEqual(rep['status'],'needs_review')
    def test_13_fallback_segments(self):
        a={'segments':[{'words':[{'word':'Hello','start':0.1,'end':0.4}]}]}
        out,rep=convert(source(['Hello']),a)
        self.assertEqual(out['paragraphs'][0][0]['timed_units'][0],['Hello',0.1,0.4])
        self.assertEqual(rep['status'],'passed')
    def test_14_conflicting_word_sources_review(self):
        a=whisper(('Hello',0.1,0.4))
        a['segments']=[{'words':[{'word':'World','start':0.1,'end':0.4}]}]
        _,rep=convert(source(['Hello']),a)
        self.assertEqual(rep['status'],'needs_review')
    def test_15_reproducible_file_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); src=d/'s.json';aud=d/'a.json';o=d/'out.json';r=d/'r.json'
            src.write_text(json.dumps(source(['A good test.'])),encoding='utf-8')
            aud.write_text(json.dumps(whisper(('A',1,1.2),('good',1.3,1.6),('test',1.7,2.0))),encoding='utf-8')
            rc1=module.run(src,aud,o,r); data1=o.read_bytes();rep1=r.read_bytes()
            rc2=module.run(src,aud,o,r)
            self.assertEqual(rc1,0);self.assertEqual(rc2,0)
            self.assertEqual(data1,o.read_bytes());self.assertEqual(rep1,r.read_bytes())
    def test_16_no_overwriting_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); src=d/'s.json';aud=d/'a.json';r=d/'r.json';
            src.write_text(json.dumps(source(['A test'])),encoding='utf-8')
            aud.write_text(json.dumps(whisper(('A',1,1.2),('test',1.3,1.6))),encoding='utf-8')
            raw=src.read_bytes()
            rc=module.run(src,aud,src,r)
            self.assertEqual(rc,1)
            self.assertEqual(src.read_bytes(),raw)
    def test_17_public_fixture_expected_outputs(self):
        fixtures=ROOT/'tests/fixtures'
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); o=d/'script.json'; r=d/'alignment-report.json'
            self.assertEqual(module.run(fixtures/'source.json',fixtures/'whisperx.json',o,r),0)
            self.assertEqual(json.loads(o.read_text(encoding='utf-8')), json.loads((fixtures/'expected-script.json').read_text(encoding='utf-8')))
            self.assertEqual(json.loads(r.read_text(encoding='utf-8')), json.loads((fixtures/'expected-report.json').read_text(encoding='utf-8')))
    def test_18_repository_layout_and_schema(self):
        self.assertTrue((ROOT/'SKILL.md').is_file())
        self.assertTrue((ROOT/'scripts/align.py').is_file())
        schema=json.loads((ROOT/'references/script.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(schema['required'], ['filetype','title','paragraphs'])
        self.assertEqual(schema['properties']['filetype']['const'], 'script')
        self.assertEqual(schema['properties']['paragraphs']['type'],'array')

    def test_19_required_filetype_and_order(self):
        out, rep = convert(source(['Hello world']), whisper(('Hello',1,1.2),('world',1.3,1.5)))
        self.assertEqual(list(out), ['filetype','title','paragraphs'])
        self.assertEqual(out['filetype'], 'script')
        self.assertEqual(rep['status'], 'passed')

    def test_20_missing_or_invalid_filetype_rejected(self):
        title, paras, tokens = module.parse_source(source(['Hello']))
        aud, notices = module.parse_audio(whisper(('Hello',1,1.5)))
        matches, missing, extra = module.align(tokens, aud)
        out, report = module.prepare_output(title, paras, tokens, aud, matches, missing, extra, notices)
        self.assertEqual(report['status'],'passed')
        for invalid in (None, 'passage', '', 5):
            modified = dict(out)
            if invalid is None:
                modified.pop('filetype')
            else:
                modified['filetype'] = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    module.validate_output(modified, paras, tokens)

if __name__=='__main__':
    unittest.main(verbosity=2)
