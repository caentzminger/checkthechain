import ast
import functools
import os

from ctc.toolbox import backend_utils
from ctc.toolbox import filesystem_utils
from ctc import config_utils
from ... import block_utils
from ... import binary_utils
from ... import contract_abi_utils
from ... import evm_spec


#
# # paths
#


def get_events_root():
    config = config_utils.get_config()
    return os.path.join(config['evm_root'], 'events')


def get_events_contract_dir(contract_address):
    contract_address = contract_address.lower()
    return os.path.join(get_events_root(), 'contract__' + contract_address)


def get_events_event_dir(contract_address, event_hash=None, event_name=None):
    contract_address = contract_address.lower()
    if event_hash is None:
        event_hash = _event_name_to_event_hash(
            contract_address=contract_address,
            event_name=event_name,
        )
        event_hash = event_hash.lower()
    contract_dir = get_events_contract_dir(contract_address)
    return os.path.join(contract_dir, 'event__' + event_hash)


def get_events_filepath(
    contract_address, start_block, end_block, event_hash=None, event_name=None
):
    contract_address = contract_address.lower()

    if event_hash is None:
        event_hash = _event_name_to_event_hash(
            contract_address=contract_address,
            event_name=event_name,
        )
    event_hash = event_hash.lower()

    subpath = evm_spec.filesystem_layout['evm_events_path'].format(
        contract_address=contract_address,
        event_hash=event_hash,
        start_block=start_block,
        end_block=end_block,
    )
    return os.path.join(config_utils.get_config()['evm_root'], subpath)


def _event_name_to_event_hash(event_name, contract_address):
    if event_name is None:
        raise Exception('must specify event_name')
    contract_abi = contract_abi_utils.get_contract_abi(
        contract_address=contract_address
    )
    candidates = []
    for entry in contract_abi:
        if entry['type'] == 'event' and entry.get('name') == event_name:
            candidates.append(entry)
    if len(candidates) == 1:
        return contract_abi_utils.get_event_hash(event_abi=candidates[0])
    elif len(candidates) > 1:
        raise Exception('found multiple events with name: ' + str(event_name))
    else:
        raise Exception('could not find hash for event: ' + str(event_name))


#
# # list saved data
#


def list_events_contracts():
    contracts = []
    events_root = get_events_root()
    if not os.path.isdir(events_root):
        return []
    for contract_dir in os.listdir(events_root):
        contract_address = contract_dir.split('__')[-1]
        contracts.append(contract_address)
    return contracts


def list_contract_events(
    contract_address,
    event_hash=None,
    event_name=None,
    allow_missing_blocks=False,
):
    contract_address = contract_address.lower()

    query_event_hash = None
    if event_name is not None:
        query_event_hash = _event_name_to_event_hash(
            event_name=event_name, contract_address=contract_address
        )
    if event_hash is not None:
        query_event_hash = event_hash

    # compile path data
    contract_dir = get_events_contract_dir(contract_address)
    paths = {}
    if not os.path.isdir(contract_dir):
        return {}
    for event_dirname in os.listdir(contract_dir):
        event_dir = os.path.join(contract_dir, event_dirname)
        _, event_hash = event_dirname.split('__')
        if query_event_hash is not None and event_hash != query_event_hash:
            continue
        for filename in os.listdir(event_dir):
            path = os.path.join(event_dir, filename)
            start_block, _, end_block = os.path.splitext(filename)[0].split(
                '__'
            )
            paths.setdefault(event_hash, {})
            paths[event_hash][path] = [int(start_block), int(end_block)]

    import numpy as np

    # create block_range and block_mask
    events = {}
    for event_hash in paths.keys():

        # gather start and end blocks
        start_blocks = []
        end_blocks = []
        for path, (start_block, end_block) in paths[event_hash].items():
            start_blocks.append(start_block)
            end_blocks.append(end_block)

        # create block_range
        min_block = min(start_blocks)
        max_block = max(end_blocks) + 1
        block_range = np.arange(min_block, max_block)

        # create block_mask
        n_blocks = block_range.size
        block_mask = np.zeros(n_blocks)
        for path, (start_block, end_block) in paths[event_hash].items():
            start_index = start_block - min_block
            end_index = n_blocks - (max_block - end_block) + 1
            block_mask[start_index:end_index] += 1
        if (block_mask > 1).sum() > 0:
            raise Exception('overlapping chunks')
        block_mask = block_mask.astype(bool)

        # check if blocks missing
        missing_blocks = block_mask.sum() != n_blocks
        if missing_blocks and not allow_missing_blocks:
            raise Exception('missing blocks')

        events[event_hash] = {
            'paths': paths[event_hash],
            'block_range': block_range,
            'block_mask': block_mask,
            'missing_blocks': missing_blocks,
        }

    return events


def list_events(
    contract_address,
    event_hash=None,
    event_name=None,
    allow_missing_blocks=False,
):
    if event_hash is None and event_name is None:
        raise Exception('must specify either event_hash or event_name')

    contract_events = list_contract_events(
        contract_address=contract_address,
        event_hash=event_hash,
        event_name=event_name,
        allow_missing_blocks=allow_missing_blocks,
    )
    if len(contract_events) == 1:
        event_hash = list(contract_events.keys())[0]
        return contract_events[event_hash]
    else:
        return None


def list_contracts_events(**kwargs):
    contracts_events = {}
    for contract_address in list_events_contracts():
        contracts_events[contract_address] = list_contract_events(
            contract_address=contract_address, **kwargs
        )
    return contracts_events


#
# # disk
#


def print_events_summary():
    print_events_summary_filesystem()


def print_events_summary_filesystem():
    contracts_events = list_contracts_events()
    print('## Contracts (' + str(len(contracts_events)) + ')')
    for contract_address in sorted(contracts_events.keys()):
        n_events = len(contracts_events[contract_address])
        print('-', contract_address, '(' + str(n_events) + ' events)')
        contract_events = contracts_events[contract_address]
        for event_hash, event_data in contract_events.items():
            block_range = [
                event_data['block_range'][0],
                event_data['block_range'][-1],
            ]
            n_files = str(len(event_data['paths']))
            dirpath = get_events_event_dir(
                contract_address=contract_address, event_hash=event_hash
            )
            n_bytes = filesystem_utils.get_directory_nbytes(dirpath)
            short_hash = event_hash[:6] + '...' + event_hash[-6:]
            print(
                '    -',
                short_hash,
                block_range,
                '(' + n_bytes + 'B in ' + n_files + ' files)',
            )


def save_events_to_filesystem(
    events,
    contract_address,
    start_block,
    end_block,
    event_hash=None,
    event_name=None,
    overwrite=False,
    verbose=True,
):
    contract_address = contract_address.lower()

    # compute path
    path = get_events_filepath(
        contract_address=contract_address,
        event_hash=event_hash,
        event_name=event_name,
        start_block=start_block,
        end_block=end_block,
    )
    if os.path.exists(path) and not overwrite:
        raise Exception('path already exists, use overwrite=True')

    if verbose:
        print('saving events to file:', path)

    # save
    os.makedirs(os.path.dirname(path), exist_ok=True)
    events.to_csv(path)


def get_events_from_filesystem(
    contract_address,
    event_hash=None,
    event_name=None,
    verbose=True,
    start_block=None,
    end_block=None,
):

    start_block, end_block = block_utils.normalize_block_range(
        start_block=start_block,
        end_block=end_block,
    )

    if event_hash is None:
        event_hash = _event_name_to_event_hash(
            event_name=event_name,
            contract_address=contract_address,
        )
    events = list_contract_events(
        contract_address=contract_address,
        event_hash=event_hash,
    )
    dfs = []
    if event_hash not in events or len(events[event_hash]['paths']) == 0:
        raise backend_utils.DataNotFound('no files for event')
    if verbose:
        if len(events[event_hash]['paths']) > 0:
            example_path = list(events[event_hash]['paths'].keys())[0]
            dirpath = os.path.dirname(example_path)
            n_bytes = filesystem_utils.get_directory_nbytes(dirpath)
            n_files = len(events[event_hash]['paths'])
        else:
            n_bytes = '0'
            n_files = '0'
        print('loading events (' + n_bytes + 'B', 'across', n_files, 'files)')
        if verbose >= 2:
            for path in events[event_hash]['paths']:
                print('-', path)

    import pandas as pd

    for path in events[event_hash]['paths'].keys():
        df = pd.read_csv(path)
        df = df.set_index(['block_number', 'transaction_index', 'log_index'])
        dfs.append(df)
    df = pd.concat(dfs, axis=0)
    df = df.sort_index()

    # trim unwanted
    if start_block == 'latest' or end_block == 'latest':
        latest_block = block_utils.get_block_number('latest')
        if start_block == 'latest':
            start_block = latest_block
        if end_block == 'latest':
            end_block = latest_block

    if start_block is not None:
        if start_block < events[event_hash]['block_range'][0]:
            raise backend_utils.DataNotFound(
                'start_block outside of filesystem contents'
            )
        mask = df.index.get_level_values(level='block_number') >= start_block
        df = df[mask]
    if end_block is not None:
        if end_block > events[event_hash]['block_range'][-1]:
            raise backend_utils.DataNotFound(
                'end_block outside of filesystem contents'
            )
        mask = df.index.get_level_values(level='block_number') <= end_block
        df = df[mask]

    # convert any bytes
    prefix = 'arg__'
    event_abi = contract_abi_utils.get_event_abi(
        contract_address=contract_address,
        event_name=event_name,
        event_hash=event_hash,
    )
    for arg in event_abi['inputs']:
        if arg['type'] in ['bytes32']:
            column = prefix + arg['name']
            lam = functools.partial(
                binary_utils.convert_binary_format, output_format='prefix_hex',
            )
            df[column] = df[column].map(ast.literal_eval).map(lam)

    return df

