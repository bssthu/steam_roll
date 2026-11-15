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
import zlib
from unittest.mock import patch

from app import Server
from steam_roll.formats import FormatError, appinfo, text_vdf
from steam_roll.library import LibraryError, collections, cover_path, load_library, online_cover, visible_on_this_client
from steam_roll.licenses import License, cache_bytes, decode_licenses, licensed_apps, packageinfo


def wire_int(number):
    number &= 0xffffffffffffffff
    out = bytearray()
    while number >= 128:
        out.append((number & 127) | 128)
        number >>= 7
    out.append(number)
    return bytes(out)


def encode_licenses(records, account=123, extra=b''):
    message = b'\x08\x01'
    for fields in records:
        record = b''.join(wire_int(key << 3) + wire_int(value) for key, value in fields.items()) + extra
        message += b'\x12' + wire_int(len(record)) + record
    return cache_bytes(message + struct.pack('<I', zlib.crc32(message)), account)


def encode_packages(records, version=40):
    def obj(value):
        out = b''
        for key, item in value.items():
            out += (b'\x00' if isinstance(item, dict) else b'\x02' if isinstance(item, int) else b'\x01') + str(key).encode() + b'\0'
            out += obj(item) if isinstance(item, dict) else struct.pack('<I', item) if isinstance(item, int) else str(item).encode() + b'\0'
        return out + b'\x08'
    out = struct.pack('<II', 0x06565500 + version, 1)
    for pid, value in records.items():
        out += struct.pack('<I', pid) + b'\0' * (32 if version == 40 else 24) + obj({str(pid): value})
    return out + b'\xff' * 4


def encode_appinfo(records, version=41, extras=None):
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
        payload=obj({'appinfo':{'common':common,**(extras or {}).get(aid,{})}})
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
        self.set_licenses([{1:101,7:512,9:1}])
        self.set_packages({101:{'appids':{str(i):i for i in range(1,6)}}})

    def write(self,p,data):
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data if isinstance(data,bytes) else data.encode())

    def set_cloud(self,data):self.write(self.cloud,json.dumps(data))
    def set_licenses(self,records):self.write(self.user/'config/licensecache',encode_licenses(records))
    def set_packages(self,records):self.write(self.root/'appcache/packageinfo.vdf',encode_packages(records))
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

    def test_removed_and_expired_licenses_cannot_be_revived_by_history_groups_or_install(self):
        self.set_licenses([{1:101,7:520,9:1}])
        data,covers=load_library(self.root,'123')
        self.assertEqual(data['games'],[])
        self.assertEqual(covers,{})
        self.set_licenses([])
        self.assertEqual(load_library(self.root,'123')[0]['games'],[])

    def test_uninstalled_owned_game_and_game_demo_stay_but_software_demo_is_excluded(self):
        self.set_packages({101:{'appids':{'0':1,'1':5,'2':7,'3':8}}})
        self.write(self.root/'appcache/appinfo.vdf',encode_appinfo({
            1:{'name':'Owned uninstalled game','type':'Game'},
            5:{'name':'Useful game demo','type':'Demo','parent':10},
            7:{'name':'Software demo','type':'Demo','parent':9},
            8:{'name':'Never played owned game','type':'Game'},
            9:{'name':'Software','type':'Application'},10:{'name':'Parent game','type':'Game'},
        }))
        (self.root/'steamapps/common/one').rmdir()
        data,_=load_library(self.root,'123')
        self.assertEqual([g['id'] for g in data['games']],[1,5,8])
        self.assertFalse(data['games'][0]['installed'])
        self.assertEqual(data['games'][1]['type'],'demo')
        self.assertEqual(data['games'][2]['minutes'],0)

    def test_missing_corrupt_or_wrong_account_licenses_fail_closed(self):
        self.write(self.user/'config/licensecache',encode_licenses([{1:101,9:1}],account=456))
        with self.assertRaises(LibraryError):load_library(self.root,'123')
        self.write(self.user/'config/licensecache',b'corrupt')
        with self.assertRaises(LibraryError):load_library(self.root,'123')
        (self.user/'config/licensecache').unlink()
        with self.assertRaises(LibraryError):load_library(self.root,'123')

    def test_missing_package_does_not_fall_back_to_historical_games(self):
        self.set_packages({})
        data,_=load_library(self.root,'123')
        self.assertEqual(data['games'],[])
        self.assertTrue(data['warnings'])

    def test_default_package_only_admits_installed_games_but_individual_license_survives(self):
        self.set_licenses([{1:0,7:512,9:1}])
        self.set_packages({0:{'appids':{'0':1,'1':5}},202:{'appids':{'0':5}}})
        # App 5 has history and a collection reference, neither proves visibility.
        self.assertEqual([g['id'] for g in load_library(self.root,'123')[0]['games']],[1])
        self.set_licenses([{1:0,7:512,9:1},{1:202,7:512,9:1}])
        self.assertEqual([g['id'] for g in load_library(self.root,'123')[0]['games']],[1,5])
        self.set_licenses([{1:0,7:512,9:1},{1:202,7:520,9:1}])
        self.assertEqual([g['id'] for g in load_library(self.root,'123')[0]['games']],[1])

    def test_installed_only_metadata_also_applies_to_nondefault_licenses(self):
        self.write(self.root/'appcache/appinfo.vdf',encode_appinfo({
            1:{'name':'Installed-only game','type':'Game'},
            5:{'name':'Ordinary uninstalled game','type':'Game'},
        },extras={1:{'extended':{'visibleonlywheninstalled':'1'}}}))
        self.assertEqual([g['id'] for g in load_library(self.root,'123')[0]['games']],[1,5])
        (self.root/'steamapps/common/one').rmdir()
        self.assertEqual([g['id'] for g in load_library(self.root,'123')[0]['games']],[5])

    def test_same_name_platform_variants_keep_appid_based_group_membership(self):
        self.set_packages({101:{'appids':{'0':1,'1':5,'2':8}}})
        self.write(self.root/'appcache/appinfo.vdf',encode_appinfo({
            1:{'name':'Same title','type':'Game','oslist':'windows','visibleonlyonavailableplatforms':'1'},
            5:{'name':'Same title','type':'Game','oslist':'macos','visibleonlyonavailableplatforms':'1'},
            8:{'name':'Same title','type':'Game','oslist':'windows'},
        }))
        with patch('steam_roll.library.sys.platform','win32'):
            games=load_library(self.root,'123')[0]['games']
        self.assertEqual([g['id'] for g in games],[1,8])
        self.assertEqual(games[0]['groups'],['a'])
        self.assertEqual(games[1]['groups'],['ungrouped'])


class VisibilityTests(unittest.TestCase):
    def test_platform_support_is_only_a_visibility_filter_when_explicitly_marked(self):
        metadata={'common':{'oslist':'macos','exfgls':6}}
        self.assertTrue(visible_on_this_client(metadata,False,'windows'))
        metadata['common']['visibleonlyonavailableplatforms']='true'
        self.assertFalse(visible_on_this_client(metadata,False,'windows'))
        self.assertTrue(visible_on_this_client(metadata,False,'macos'))
        metadata['common']['oslist']='windows, macos'
        self.assertTrue(visible_on_this_client(metadata,False,'windows'))

    def test_installed_flag_and_legacy_platform_list(self):
        metadata={'common':{'visibleonlyonavailableplatforms':1},
                  'extended':{'visibleonlywheninstalled':'true','validoslist':'windows, macos'}}
        self.assertFalse(visible_on_this_client(metadata,False,'windows'))
        self.assertTrue(visible_on_this_client(metadata,True,'windows'))
        self.assertFalse(visible_on_this_client(metadata,True,'linux'))


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


class LicenseTests(unittest.TestCase):
    def test_valid_cache_only_returns_entitlement_fields(self):
        # Include unknown fixed32/fixed64/bytes plus an access token, all synthetic.
        extra=b'\x15'+struct.pack('<I',123)+b'\x91\x03'+b'\0'*8+b'\x9a\x03\x03xyz'
        encoded=encode_licenses([{1:100,7:512,9:1,17:123456789}],extra=extra)
        self.assertEqual(decode_licenses(encoded,123),[License(100,512,1)])
        self.assertNotIn('token',repr(decode_licenses(encoded,123)))
        with self.assertRaises(FormatError):decode_licenses(encoded[:-1]+bytes([encoded[-1]^1]),123)
        with self.assertRaises(FormatError):decode_licenses(encoded,456)

    def test_signed_account_ids_and_unlimited_negative_minutes(self):
        account=0xf0000012
        decoded=decode_licenses(encode_licenses([{1:100,9:1,4:-1}],account),account)
        self.assertEqual(decoded[0].minute_limit,-1)
        self.assertEqual(licensed_apps(decoded,{100:{'appids':{'0':1}}},1000)[0],{1})

    def test_package_versions_unknown_versions_and_truncation(self):
        for version in (39,40):
            blob=encode_packages({100:{'appids':{'0':1},'extended':{'expirytime':123}}},version)
            self.assertEqual(packageinfo(blob,{100})[100]['appids'],{'0':1})
            with self.assertRaises(FormatError):packageinfo(blob[:-3],{100})
        with self.assertRaises(FormatError):packageinfo(b'\0'*8,{100})

    def test_expiry_cancellation_time_limit_and_multiple_licenses(self):
        packages={i:{'appids':{'0':i}} for i in range(1,10)}
        packages[8]['extended']={'expirytime':1000}
        packages[9]['extended']={'expirytime':2000}
        licenses=[License(1,0x200,1),License(2,0x208,1),License(3,0x10,1),License(4,0x20,1),
                  License(5,0x40000,1),License(6,0,2,60,60),License(7,0,2,60,59),License(8,0,1),License(9,0,1)]
        apps,missing,expiry=licensed_apps(licenses,packages,1000)
        self.assertEqual(apps,{1,7,9});self.assertEqual(missing,set());self.assertEqual(expiry,2000)
        packages[10]={'appids':{'0':2}}
        self.assertIn(2,licensed_apps(licenses+[License(10,0,1)],packages,1000)[0])

    def test_default_package_is_not_an_individual_library_license(self):
        licenses=[License(0,512,1),License(1,512,1)]
        packages={0:{'appids':{'0':1,'1':2}},1:{'appids':{'0':2,'1':3}}}
        self.assertEqual(licensed_apps(licenses,packages,1000)[0],{1,2,3})
        self.assertEqual(licensed_apps(licenses,packages,1000,include_default_package=False)[0],{2,3})


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

    def test_changed_license_cache_revokes_old_covers_without_explicit_refresh(self):
        self.assertEqual(self.request('/api/cover/1?account=123')[0],200)
        self.set_licenses([])
        self.assertEqual(self.request('/api/cover/1?account=123')[0],404)
        self.assertEqual(json.loads(self.request('/api/library?account=123')[1])['games'],[])

    def test_clock_expiry_invalidates_cached_games_and_covers(self):
        self.set_packages({101:{'appids':{'0':1},'extended':{'expirytime':2000}}})
        with patch('time.time',return_value=1000):
            self.assertEqual(self.request('/api/cover/1?account=123')[0],200)
        with patch('time.time',return_value=2000):
            self.assertEqual(self.request('/api/cover/1?account=123')[0],404)


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
