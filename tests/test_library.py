import http.client
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from app import Server
from steam_roll.formats import FormatError, appinfo, text_vdf
from steam_roll.library import LibraryError, collections, cover_path, load_library, online_cover


def encode_appinfo(records, version=41):
    strings=[]
    def key(k):
        if version < 41:
            return k.encode()+b'\0'
        if k not in strings:strings.append(k)
        return struct.pack('<I',strings.index(k))
    def obj(d):
        out=b''
        for k,v in d.items():
            out+=(b'\0' if isinstance(v,dict) else b'\1')+key(k)
            out+=obj(v) if isinstance(v,dict) else str(v).encode()+b'\0'
        return out+b'\x08'
    entries=b''
    for aid,common in records.items():
        payload=obj({'appinfo':{'common':common}})
        header=b'\0'*(60 if version>=40 else 40)
        entries+=struct.pack('<II',aid,len(header)+len(payload))+header+payload
    entries+=b'\0'*4
    head=struct.pack('<II',0x07564400+version,1)
    if version==41:
        head+=struct.pack('<Q',16+len(entries))
        entries+=struct.pack('<I',len(strings))+b''.join(s.encode()+b'\0' for s in strings)
    return head+entries


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.user=self.root/'userdata/123'
        self.cloud=self.user/'config/cloudstorage/cloud-storage-namespace-1.json'
        self.write(self.user/'config/localconfig.vdf','"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "1" {"playTime" "90" "LastPlayed" "1700000000"} "2" {} "3" {} "4" {} "5" {} } } } } }')
        self.write(self.user/'7/remote/sharedconfig.vdf','"UserRoamingConfigStore" {"Software" {"Valve" {"Steam" {"apps" {"3" {"hidden" "1"}}}}}}')
        self.set_cloud([
            ['user-collections.hidden',{'value':json.dumps({'id':'hidden','added':[2],'removed':[]})}],
            ['user-collections.a',{'value':json.dumps({'id':'a','name':'测试组','added':[1,2,3,4,5],'removed':[5]})}],
            ['user-collections.deleted',{'is_deleted':True}],
        ])
        self.write(self.root/'appcache/appinfo.vdf',encode_appinfo({i:{'name':f'Game {i}','type':'Tool' if i==4 else 'Game'} for i in range(1,7)}))
        self.write(self.root/'steamapps/appmanifest_1.acf','"AppState" {"appid" "1" "name" "Game 1" "StateFlags" "4" "installdir" "one"}')
        self.write(self.root/'steamapps/appmanifest_6.acf','"AppState" {"appid" "6" "name" "Other account" "StateFlags" "4" "installdir" "six" "LastOwner" "76561197960266000"}')
        (self.root/'steamapps/common/one').mkdir(parents=True)
        self.write(self.root/'appcache/librarycache/1/library_600x900.jpg',b'\xff\xd8\xfftest')
        self.write(self.root/'appcache/librarycache/2/library_600x900.jpg',b'\xff\xd8\xffhidden')

    def write(self,p,data):
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data if isinstance(data,bytes) else data.encode())

    def set_cloud(self,data):self.write(self.cloud,json.dumps(data))
    def tearDown(self):self.temp.cleanup()


class LibraryTests(Fixture):
    def test_hidden_union_filter_types_and_account_isolation(self):
        data,covers=load_library(self.root,'123')
        self.assertEqual([g['id'] for g in data['games']],[1,5])
        self.assertEqual(set(covers),{1})
        one=data['games'][0]
        self.assertEqual(one['minutes'],90)
        self.assertTrue(one['installed'])
        self.assertEqual(one['groups'],['a'])
        self.assertEqual(data['games'][1]['groups'],['ungrouped'])
        self.assertNotIn('hidden', {g['id'] for g in data['groups']})

    def test_pending_hidden_updates_and_removals(self):
        pending=[['user-collections.hidden',{'value':json.dumps({'id':'hidden','added':[1,2],'removed':[2]})}]]
        self.write(self.cloud.with_name('cloud-storage-namespace-1.modified.json'),json.dumps(pending))
        hidden,_,_,_=collections(self.user)
        self.assertEqual(hidden,{1,3})

    def test_missing_or_corrupt_hidden_data_fails_closed(self):
        self.write(self.cloud,'{broken')
        with self.assertRaises(ValueError):load_library(self.root,'123')
        self.cloud.unlink()
        (self.user/'7/remote/sharedconfig.vdf').unlink()
        with self.assertRaises(LibraryError):load_library(self.root,'123')

    def test_dynamic_members_do_not_pretend_to_expand_rules(self):
        self.set_cloud([['user-collections.hidden',{'value':'{"added":[],"removed":[]}'}],['user-collections.d',{'value':'{"name":"dynamic","added":[1],"filter":{"tags":[1]}}'}]])
        data,_=load_library(self.root,'123')
        self.assertTrue(any('动态' in w for w in data['warnings']))

    def test_partial_install_or_missing_directory_is_not_installed(self):
        (self.root/'steamapps/common/one').rmdir()
        self.assertFalse(load_library(self.root,'123')[0]['games'][0]['installed'])
        (self.root/'steamapps/common/one').mkdir()
        self.write(self.root/'steamapps/appmanifest_1.acf','"AppState" {"appid" "1" "StateFlags" "2" "installdir" "one"}')
        self.assertFalse(load_library(self.root,'123')[0]['games'][0]['installed'])

    def test_invalid_account_never_becomes_a_path(self):
        with self.assertRaises(LibraryError):load_library(self.root,'../123')

    def test_hashed_cover_subdirectory_and_external_path_rejection(self):
        asset='a'*40+'/library_capsule.jpg'
        path=self.root/'appcache/librarycache/5'/asset
        self.write(path,b'\xff\xd8\xfftest')
        common={'library_assets_full':{'library_capsule':{'image':{'english':asset}}}}
        self.assertEqual(cover_path(self.root,self.user,5,common),path)
        self.assertTrue(online_cover(5,common).endswith('/5/'+asset))
        common['library_assets_full']['library_capsule']['image']['english']='../../config/loginusers.vdf'
        self.assertIsNone(cover_path(self.root,self.user,5,common))
        self.assertTrue(online_cover(5,common).endswith('/5/library_600x900.jpg'))


class FormatTests(unittest.TestCase):
    def test_binary_versions(self):
        for version in [39,40,41]:
            parsed=appinfo(encode_appinfo({42:{'name':'中文游戏','type':'Game'}},version),{42})
            self.assertEqual(parsed[42]['common']['name'],'中文游戏')

    def test_text_escapes_comments_and_case(self):
        data=text_vdf('// comment\n "Root" {"PATH" "C:\\\\Steam" "name" "a\\"b"}')
        self.assertEqual(data['root'],{'path':'C:\\Steam','name':'a"b'})
        with self.assertRaises(FormatError):text_vdf('"root" {"a" "b"')

    def test_unknown_or_truncated_binary_fails(self):
        with self.assertRaises(FormatError):appinfo(b'\0'*8,set())
        with self.assertRaises(FormatError):appinfo(encode_appinfo({1:{'name':'X'}})[:-4],{1})


class HttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server=Server(0,self.root)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def request(self,path,method='GET',headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request(method,path,headers=headers or {})
        response=connection.getresponse();body=response.read();connection.close()
        return response.status,body
    def test_http_cannot_serve_hidden_covers_or_arbitrary_files(self):
        self.assertEqual(self.request('/api/cover/2?account=123')[0],404)
        self.assertEqual(self.request('/api/cover/1?account=123')[0],200)
        self.assertEqual(self.request('/../app.py')[0],404)
        self.assertEqual(self.request('/api/library',method='POST')[0],405)
    def test_cross_site_host_and_shutdown_guard(self):
        self.assertEqual(self.request('/api/library',headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/health',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/shutdown',method='POST')[0],403)
    def test_failed_refresh_discards_old_library_and_covers(self):
        self.assertEqual(self.request('/api/library?account=123')[0],200)
        self.write(self.cloud,'broken')
        self.assertEqual(self.request('/api/library?account=123&refresh=1')[0],503)
        self.assertEqual(self.request('/api/cover/1?account=123')[0],503)


class GuardTests(unittest.TestCase):
    def test_real_audit_hook_blocks_write_delete_rename_and_process_launch(self):
        code='''
import os,sys,subprocess
from pathlib import Path
from steam_roll.readonly import install
target=Path(sys.argv[1]); install()
operations=[lambda:target.write_bytes(b'changed'),lambda:os.open(target,os.O_WRONLY),lambda:target.unlink(),lambda:target.rename(target.with_suffix('.moved')),lambda:target.parent.joinpath('new').mkdir(),lambda:os.utime(target,None),lambda:subprocess.Popen([sys.executable,'-V'])]
for action in operations:
    try: action()
    except PermissionError: pass
    else: raise AssertionError('A mutation was allowed')
assert target.read_bytes()==b'original'
print('READ_ONLY_GUARD_PASS')
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'protected';path.write_bytes(b'original')
            result=subprocess.run([sys.executable,'-B','-c',code,str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('READ_ONLY_GUARD_PASS',result.stdout)
            self.assertEqual(path.read_bytes(),b'original')


if __name__=='__main__':unittest.main()
