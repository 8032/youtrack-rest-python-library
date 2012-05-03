from sync.links import LinkSynchronizer, IssueBinder, LinkImporter
from youtrack import YouTrackException
from youtrack.connection import Connection
from youtrack2youtrack import youtrack2youtrack
from datetime import datetime
import time
import csv

VERBOSE_MODE = False

project_id = "JT"
tag = "sync"
fields_to_sync = ['state', 'type', 'priority', 'subsystem', 'assignee', 'fix versions', 'affected versions']
priority_mapping= {'0':'Show-stopper', '1':'Critical', '2':'Major', '3':'Normal', '4':'Minor'}
field_name_mapping = {'assignee':'assigneeName', 'fix versions':'fixVersions', 'affected versions':'affectedVersions'}
query = "tag: " + tag

master_url = "http://unit-1"
master_root_login = "root"
master_root_password = "root"
slave_url = "http://unit-2"
slave_root_login = "root"
slave_root_password = "root"

batch = 100
sync_map_name = 'sync_map'
slave_to_master_map = {}
config_name = 'sync_config'
config_time_format = '%Y-%m-%d %H:%M:%S:%f'
query_time_format = '%m-%dT%H:%M:%S'
master_sync_field_name = 'Sync with'
empty_field_text = 'No sync with'

last_run = datetime(2012, 1, 1) #default value
current_run = datetime.now()

csv.register_dialect('mapper', delimiter=':', quoting=csv.QUOTE_NONE)

def read_sync_map():
    try:
        with open(sync_map_name, 'r') as sync_map_file:
            reader = csv.reader(sync_map_file, 'mapper')
            result = {}
            for row in reader:
              result[row[0]] = row[1]
            return result
    except StopIteration:
        return {}
    except IOError:
        with open(sync_map_name, 'w') as sync_map_file:
            sync_map_file.write('')
        return {}

def write_sync_map(ids_map):
    with open(sync_map_name, 'w') as sync_map_file:
        writer = csv.writer(sync_map_file, 'mapper')
        for key in ids_map.keys():
            writer.writerow([key, ids_map[key]])

def read_last_run():
    try:
        with open(config_name, 'r') as config_file:
            try:
                return datetime.strptime(config_file.readline(), config_time_format)
            except ValueError:
                return datetime(2012, 1, 1)
    except IOError:
        with open(config_name, 'w') as config_file:
            config_file.write('')
        return datetime(2012, 1, 1)

def get_formatted_for_query(_datetime):
    return _datetime.strftime(query_time_format)

def get_in_milliseconds(_datetime):
    return int(round(1e+3*time.mktime(_datetime.timetuple()) + 1e-3*_datetime.microsecond))

def get_issue_changes(yt, issue, after, before):
#    yt.headers['Accepts'] = 'application/json;charset=utf-8'
    result = yt.get_changes_for_issue(issue.id)
    after_ms = get_in_milliseconds(after)
    before_ms = get_in_milliseconds(before)
    new_changes = []
    for change in result:
        change_time = change['updated']
        if (change_time > after_ms) and (change_time < before_ms):
            new_changes.append(change)
    return new_changes

def executeSyncCommand(yt, issue_id, command, comment=None, run_as=None):
    if command != '':
        if not VERBOSE_MODE:
            try:
                yt.executeCommand(issue_id, command, comment=comment, run_as=run_as)
            except YouTrackException, e:
                print e
        yt_name = 'Master' if yt == master else 'Slave'
        user_login = (master_root_login if yt == master else slave_root_login) if run_as is None else run_as
        print '[Sync, ' + issue_id + ' in ' + yt_name + '] applied command: \"' + command + '\" on behalf of ' + user_login

def apply_changes_to_issue(to_yt, from_yt, issue_id, changes, fields_to_ignore=None):
    if not fields_to_ignore: fields_to_ignore = []
    changed_fields = set()
    for change in changes:
        run_as = change.updater_name
        try:
            to_yt.getUser(run_as)
        except YouTrackException:
            to_yt.importUsers([from_yt.getUser(run_as)])
        comment = None
        if len(change.comments):
            comment = change.comments[0]
        command = ''
        for field in change.fields:
            field_name = field.name.lower()
            if field.name != 'links' and field_name in fields_to_sync and field_name not in fields_to_ignore:
                for field_value in field.new_value:
                    changed_fields.add(field_name)
                    command += get_command_set_value_to_field(field_name, field_value)
        executeSyncCommand(to_yt, issue_id, command, comment, run_as)
    return changed_fields

def get_command_set_value_to_field(field, field_value):
    command = ""
    if len(field_value):
        if isinstance(field_value, list):
            for value in field_value:
                command += field + " " + value + " "
        else:
            if field == 'priority' and field_value in priority_mapping.keys():
                field_value = priority_mapping[field_value]
            command += field + " " + field_value + " "
    return command

def apply_changes_to_new_issue(yt, issue_id_to_apply, original_issue):
    command = ''
    for field in fields_to_sync:
        internal_field = field_name_mapping.get(field) if field_name_mapping.has_key(field) else field
        if hasattr(original_issue, internal_field):
            field_value = original_issue[internal_field]
            command += get_command_set_value_to_field(field, field_value)
    executeSyncCommand(yt, issue_id_to_apply, command, None, None)
    for comment in original_issue.getComments():
        executeSyncCommand(yt, issue_id_to_apply, "comment", comment.text, comment.author)

def add_sync_comment(yt, issue_id, comment_text, run_as):
    if comment_text is not None and comment_text != '':
        if not VERBOSE_MODE:
            try:
                yt.executeCommand(issue_id, '', comment=comment_text, run_as=run_as)
            except YouTrackException, e:
                print e
        yt_name = 'Master' if yt == master else 'Slave'
        user_login = (master_root_login if yt == master else slave_root_login) if run_as is None else run_as
        print '[Sync, ' + issue_id + ' in ' + yt_name + '] added comment: \"' + comment_text[0:8] + '...\" from ' + user_login

def merge_and_apply_changes(left_yt, left_issue_id, left_changes, right_yt, right_issue_id, right_changes):
    changed_fields = apply_changes_to_issue(right_yt, left_yt, right_issue_id, left_changes)
    apply_changes_to_issue(left_yt, right_yt, left_issue_id, right_changes, changed_fields)

def create_and_attach_sync_field(yt, sync_project_id, sync_field_name):
    sync_field_created = any(field.name == sync_field_name for field in yt.getCustomFields())
    if not sync_field_created:
        yt.createCustomFieldDetailed(sync_field_name, "integer", False, True)
    sync_field_attached = any(field.name == sync_field_name for field in yt.getProjectCustomFields(sync_project_id))
    if not sync_field_attached:
        yt.createProjectCustomFieldDetailed(project_id, sync_field_name, empty_field_text)

def clone_issue(yt_to, issue_from):
    try:
        safe_summary = issue_from.summary if hasattr(issue_from, 'summary') else ''
        safe_description = issue_from.description if hasattr(issue_from, 'description') else ''
        created_issue_number = yt_to.createIssue(project_id, None, safe_summary, safe_description).rpartition('-')[2]
        #fail if summary or description are too long
    except YouTrackException, e:
        created_issue_number = None
        print e

    yt_name = 'Master' if yt_to == master else 'Slave'
    if created_issue_number:
        created_issue_id = project_id + '-' + created_issue_number
        print '[Sync, ' + created_issue_id + ' in ' + yt_name + '] created'
        apply_changes_to_new_issue(yt_to, created_issue_id, issue_from)
        return created_issue_id
    else:
        yt_opp = 'Master' if yt_to == slave else 'Slave'
        print '[Sync, ' + issue_from.id + ' in ' + yt_opp + '] failed to import to ' + yt_name
        return None

def import_to_master(slave_issue):
    master_issue_id = clone_issue(master, slave_issue)
    if master_issue_id:
        slave_to_master_map[str(slave_issue.id)] = master_issue_id
        executeSyncCommand(master, master_issue_id, "tag " + tag)
        executeSyncCommand(slave, slave_issue.id, master_sync_field_name + " " + master_issue_id.rpartition('-')[2])
    return master_issue_id

def sync_to_master(slave_issue):
    if hasattr(slave_issue, master_sync_field_name):
        master_issue = master.getIssue(project_id + '-' + slave_issue[master_sync_field_name])
        slave_changes = get_issue_changes(slave, slave_issue, last_run, current_run)
        master_changes = get_issue_changes(master, master_issue, last_run, current_run)
        changed_fields = apply_changes_to_issue(slave, master, slave_issue.id, master_changes)
        apply_changes_to_issue(master, slave, master_issue.id, slave_changes, changed_fields)
        return master_issue.id
    else:
        return import_to_master(slave_issue)

def import_to_slave(master_issue):
    slave_issue_id = clone_issue(slave, master_issue)
    if slave_issue_id:
        slave_to_master_map[slave_issue_id] = str(master_issue.id)
        executeSyncCommand(slave, slave_issue_id, master_sync_field_name + " " + master_issue.numberInProject)
        executeSyncCommand(slave, slave_issue_id, "tag " + tag)
    return slave_issue_id

def sync_to_slave(master_issue):
    slave_issues = slave.getIssues(project_id, master_sync_field_name + ": " + master_issue.numberInProject, 0, 1)
    if len(slave_issues):
        slave_issue = slave_issues[0]
        master_changes = get_issue_changes(master, master_issue, last_run, current_run)
        apply_changes_to_issue(slave, master, slave_issue.id, master_changes)
        return slave_issue.id
    else:
        return import_to_slave(master_issue)

def apply_to_issues(issues_getter, action, excluded_ids=None, log_header=''):
    if not issues_getter or not action: return
    start = 0
    print log_header + ' Started'
    issues = issues_getter(start, batch)
    processed_issue_ids_set = set([])
    while len(issues):
        for issue in issues:
            sync_id = str(issue.id)
            if not (excluded_ids and sync_id in excluded_ids):
                action(issue)
                processed_issue_ids_set.add(sync_id)
        print log_header + ' Processed ' + str(start + len(issues)) + ' issues'
        start += batch
        issues = issues_getter(start, batch)
    print log_header + ' Action applied to ' + str(len(processed_issue_ids_set)) + ' issues'
    return processed_issue_ids_set

def get_tagged_only_in_slave(start, batch):
    rq = query + ' ' + master_sync_field_name + ':  {' + empty_field_text + '}'
    return slave.getIssues(project_id, rq, start, batch)

def get_tagged_in_master(start, batch):
    rq = query
    return master.getIssues(project_id, rq, start, batch)

def get_updated_in_slave_from_last_run(start, batch):
    rq = query + ' updated: ' + get_formatted_for_query(last_run) + " .. " + get_formatted_for_query(current_run)
    return slave.getIssues(project_id, rq, start, batch)

def get_updated_in_master_from_last_run(start, batch):
    rq = query + ' updated: ' + get_formatted_for_query(last_run) + " .. " + get_formatted_for_query(current_run)
    return master.getIssues(project_id, rq, start, batch)

def get_project(slave, project_id):
    try:
        return slave.getProject(project_id)
    except YouTrackException:
        return None

def slave_ids_set_to_sync_ids_set(ids):
    return set([slave_to_master_map[id] for id in ids])


def import_project(slave, project_id):
    youtrack2youtrack(master_url, master_root_login, master_root_password, slave_url, slave_root_login, slave_root_password, [project_id], query)
    create_and_attach_sync_field(slave, project_id, master_sync_field_name)
    start = 0
    issues = slave.getIssues(project_id, '', start, batch)
    while len(issues):
        for issue in issues:
            executeSyncCommand(slave, issue.id, master_sync_field_name + " " + issue.numberInProject)
            executeSyncCommand(slave, issue.id, "tag " + tag)
            slave_to_master_map[str(issue.id)] = str(issue.id)
        start += batch
        issues = slave.getIssues(project_id, '', start, batch)

def merge_links_and_comments(slave, master, s_to_m):
#    issue_binder = IssueBinder(s_to_m)
#    slave_importer = LinkImporter(slave, project_id, query)
#    master_importer = LinkImporter(master, project_id, query)
#    link_synchronizer = LinkSynchronizer(master_importer, slave_importer, issue_binder)
#    link_synchronizer.setVerboseMode(VERBOSE_MODE)

    print "[Sync, comment synchronisation] Started...."
    counter = 0
    for issue_id in s_to_m.keys():
        slave_issue = slave.getIssue(issue_id)
        master_issue = master.getIssue(s_to_m[issue_id])
        merge_comments(slave_issue.id, master_issue.id)
#        link_synchronizer.collectLinksToSync(slave_issue, master_issue)
        counter += 1
        if counter % batch == 0:
            print "[Sync, comment synchronisation] Processed " + counter + " issues"
#    print "[Sync, link synchronisation] Started...."
#    link_synchronizer.syncCollectedLinks()

def merge_comments(slave_id, master_id):
    slave_comments = slave.getComments(slave_id)
    master_comments = master.getComments(master_id)
    if len(slave_comments) or len(master_comments):
        master_texts = set([cm.text[0:8] for cm in master_comments])
        slave_texts = set([cm.text[0:8] for cm in slave_comments])
        slave_unique = [cm for cm in slave_comments if cm.text[0:8] not in master_texts]
        master_unique = [cm for cm in master_comments if cm.text[0:8] not in slave_texts]
        for cm in slave_unique:
            add_sync_comment(master, master_id, cm.text, cm.author)
        for cm in master_unique:
            add_sync_comment(slave, slave_id, cm.text, cm.author)


#VERBOSE_MODE = True

last_run = read_last_run()
slave_to_master_map = read_sync_map()

master = Connection(master_url, master_root_login, master_root_password)
slave = Connection(slave_url, slave_root_login, slave_root_password)

try:
    if get_project(slave, project_id):
        create_and_attach_sync_field(slave, project_id, master_sync_field_name)

        #1. synchronize sync-issues in slave which have no synchronized clone in master
        imported_slave_ids_set = apply_to_issues(get_tagged_only_in_slave,
            import_to_master,
            log_header='[Sync, Importing new issues from slave to master]')

        #2. synchronize sync-issues in master which have no synchronized clone in slave
        sync_set = set(slave_to_master_map.values())
        imported_master_ids_set = apply_to_issues(get_tagged_in_master,
            import_to_slave,
            excluded_ids=sync_set,
            log_header='[Sync, Importing new issues from master to slave]')

        #3. synchronize sync-issues updated in slave which have synchronized clone in master
        updated_slave_ids_set = apply_to_issues(get_updated_in_slave_from_last_run,
            sync_to_master,
            excluded_ids=imported_slave_ids_set,
            log_header='[Sync, Merging sync issues updated in slave]')

        #4. synchronize sync-issues updated in master which have synchronized clone in slave (if clone hasn't been updated)
        updated_master_ids_set = slave_ids_set_to_sync_ids_set(updated_slave_ids_set) | imported_master_ids_set
        apply_to_issues(get_updated_in_master_from_last_run,
            sync_to_slave,
            excluded_ids=updated_master_ids_set,
            log_header='[Sync, Merging sync issues updated in master and unchanged in slave]')

        #links and comments synchronization
        merge_links_and_comments(slave, master, slave_to_master_map)
    else:
        import_project(slave, project_id)

except Exception, e:
    print e
finally:
    #write set of master-ids of synchronized issues
    write_sync_map(slave_to_master_map)

    #write time of script evaluation finish as last run time
    with open(config_name, 'w') as config_file:
        config_file.write(datetime.now().strftime(config_time_format))