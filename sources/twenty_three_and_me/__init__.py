"""
23andMe genotyping data extraction.

Copyright (C) 2014 PersonalGenomes.org

This software is shared under the "MIT License" license (aka "Expat License"),
see LICENSE.TXT for full license text.
"""

import bz2
import logging
import os
import re
import shutil
import urlparse

from cStringIO import StringIO
from datetime import date, datetime

import arrow
import bcrypt

from base_source import BaseSource

logger = logging.getLogger(__name__)

REF_23ANDME_FILE = os.path.join(os.path.dirname(__file__), 'reference_b37.txt')

# Was used to generate reference genotypes in the previous file.
REFERENCE_GENOME_URL = ('http://hgdownload-test.cse.ucsc.edu/' +
                        'goldenPath/hg19/bigZips/hg19.2bit')

VCF_FIELDS = ['CHROM', 'POS', 'ID', 'REF', 'ALT', 'QUAL', 'FILTER',
              'INFO', 'FORMAT', '23ANDME_DATA']


def vcf_header(source=None, reference=None, format_info=None):
    """
    Generate a VCF header.
    """
    header = []
    today = date.today()

    header.append('##fileformat=VCFv4.1')
    header.append('##fileDate=%s%s%s' % (str(today.year),
                                         str(today.month).zfill(2),
                                         str(today.day).zfill(2)))

    if source:
        header.append('##source=' + source)

    if reference:
        header.append('##reference=%s' % reference)

    for item in format_info:
        header.append('##FORMAT=' + item)

    header.append('#' + '\t'.join(VCF_FIELDS))

    return header


def vcf_from_raw_23andme(raw_23andme):
    output = StringIO()
    reference = dict()

    with open(REF_23ANDME_FILE) as f:
        for line in f:
            data = line.rstrip().split('\t')

            if data[0] not in reference:
                reference[data[0]] = dict()

            reference[data[0]][data[1]] = data[2]

    header = vcf_header(
        source='open_humans_data_processing.twenty_three_and_me',
        reference=REFERENCE_GENOME_URL,
        format_info=['<ID=GT,Number=1,Type=String,Description="Genotype">'])

    for line in header:
        output.write(line + '\n')

    for line in raw_23andme:
        # Skip header
        if line.startswith('#'):
            continue

        data = line.rstrip().split('\t')

        # Skip uncalled and genotyping without explicit base calls
        if not re.match(r'^[ACGT]{1,2}$', data[3]):
            continue
        vcf_data = {x: '.' for x in VCF_FIELDS}

        # Chromosome, position, dbSNP ID, reference. Skip if we don't have ref.
        try:
            vcf_data['REF'] = reference[data[1]][data[2]]
        except KeyError:
            continue

        if data[1] == 'MT':
            vcf_data['CHROM'] = 'M'
        else:
            vcf_data['CHROM'] = data[1]

        vcf_data['POS'] = data[2]

        if data[0].startswith('rs'):
            vcf_data['ID'] = data[0]

        # Figure out the alternate alleles.
        alt_alleles = []

        for alle in data[3]:
            if alle != vcf_data['REF'] and alle not in alt_alleles:
                alt_alleles.append(alle)

        if alt_alleles:
            vcf_data['ALT'] = ','.join(alt_alleles)
        else:
            vcf_data['ALT'] = '.'
            vcf_data['INFO'] = 'END=' + vcf_data['POS']

        # Get allele-indexed genotype.
        vcf_data['FORMAT'] = 'GT'
        all_alleles = [vcf_data['REF']] + alt_alleles
        genotype_indexed = '/'.join([str(all_alleles.index(x))
                                     for x in data[3]])
        vcf_data['23ANDME_DATA'] = genotype_indexed
        output_line = '\t'.join([vcf_data[x] for x in VCF_FIELDS])
        output.write(output_line + '\n')

    return output


class TwentyThreeAndMeSource(BaseSource):
    """
    Create clean file in 23andme format from downloaded version

    Obsessively careful processing that ensures 23andMe file format changes
    won't inadvertantly result in unexpected information, e.g. names.
    """

    source = 'twenty_three_and_me'

    def clean_raw_23andme(self):
        input_file = self.open_archive()

        output = StringIO()

        dateline = input_file.next()

        re_datetime_string = (r'([A-Z][a-z]{2} [A-Z][a-z]{2} [ 1-9][0-9] '
                              r'[0-9][0-9]:[0-9][0-9]:[0-9][0-9] 2[0-9]{3})')

        if re.search(re_datetime_string, dateline):
            datetime_string = re.search(re_datetime_string,
                                        dateline).groups()[0]

            re_norm_day = r'(?<=[a-z])  ([1-9])(?= [0-9][0-9]:[0-9][0-9])'

            datetime_norm = re.sub(re_norm_day, r' 0\1', datetime_string)
            datetime_23andme = datetime.strptime(datetime_norm,
                                                 '%a %b %d %H:%M:%S %Y')

            output.write('# This data file generated by 23andMe at: {}\r\n'
                         .format(datetime_23andme.strftime(
                             '%a %b %d %H:%M:%S %Y')))

        cwd = os.path.dirname(__file__)

        header_v1 = open(os.path.join(cwd, 'header-v1.txt'), 'r').read()
        header_v2 = open(os.path.join(cwd, 'header-v2.txt'), 'r').read()

        header_lines = ''

        next_line = input_file.next()

        while next_line.startswith('#'):
            header_lines += next_line

            next_line = input_file.next()

        if (header_lines.splitlines() == header_v1.splitlines() or
                header_lines.splitlines() == header_v2.splitlines()):
            output.write(header_lines)
        else:
            self.sentry_log(
                '23andMe header did not conform to expected format.')

        bad_format = False

        while next_line:
            if re.match(r'(rs|i)[0-9]+\t[1-9XYM][0-9T]?\t[0-9]+\t[ACGT\-ID][ACGT\-ID]?', next_line):
                output.write(next_line)
            else:
                # Only report this type of format issue once.
                if not bad_format:
                    bad_format = True
                    self.sentry_log('23andMe body did not conform to expected format.')
                    logger.warn('Bad format: "%s"', next_line)

            try:
                next_line = input_file.next()
            except StopIteration:
                next_line = None

        if bad_format:
            self.sentry_log('23andMe body did not conform to expected format.')

        return output

    def should_update(self, files):
        """
        Reprocess only if source file has changed.

        We store a hash of the original filepath as metadata and check this.
        Update is deemed unnecessary if (a) processed files exist, (b) they
        have recorded orig_file_hash, (c) we verify these all match a hash of
        the source file path for this task (from self.file_url).
        """
        if not files:
            return True
        for file_data in files:
            try:
                orig_file_hash = file_data['metadata']['orig_file_hash']
            except KeyError:
                return True
            if not self.same_orig_file(orig_file_hash):
                return True
        logger.info('Update unnecessary for user "{}", source "{}".'.format(
            self.oh_username, self.source))
        return False

    def same_orig_file(self, orig_file_hash):
        """
        Check hashed self.file_url path against stored orig_file_hash.
        """
        if not self.file_url:
            return False
        url_path = str(urlparse.urlparse(self.file_url).path)
        new_hash = bcrypt.hashpw(url_path, str(orig_file_hash))
        return orig_file_hash == new_hash

    def create_files(self):
        """
        Create Open Humans Dataset from uploaded 23andme full genotyping data

        Optional arguments:
            input_file: path to a local copy of the uploaded file
            file_url: path to an online copy of the input file
        """
        if not self.input_file:
            raise Exception('Run with either input_file or file_url')

        new_hash = ''
        if self.file_url:
            orig_path = urlparse.urlparse(self.file_url).path
            new_hash = bcrypt.hashpw(str(orig_path), bcrypt.gensalt())

        filename_base = '23andMe-genotyping'

        raw_23andme = self.clean_raw_23andme()
        raw_23andme.seek(0)
        vcf_23andme = vcf_from_raw_23andme(raw_23andme)

        # Save raw 23andMe genotyping to temp file.
        raw_filename = filename_base + '.txt'

        with open(self.temp_join(raw_filename), 'w') as raw_file:
            raw_23andme.seek(0)

            shutil.copyfileobj(raw_23andme, raw_file)

            self.temp_files.append({
                'temp_filename': raw_filename,
                'metadata': {
                    'description':
                        '23andMe full genotyping data, original format',
                    'tags': ['23andMe', 'genotyping'],
                    'orig_file_hash': new_hash,
                    'creation_date': arrow.get().format(),
                },
            })

        # Save VCF 23andMe genotyping to temp file.
        vcf_filename = filename_base + '.vcf.bz2'

        with bz2.BZ2File(self.temp_join(vcf_filename), 'w') as vcf_file:
            vcf_23andme.seek(0)

            shutil.copyfileobj(vcf_23andme, vcf_file)

            self.temp_files.append({
                'temp_filename': vcf_filename,
                'metadata': {
                    'description': '23andMe full genotyping data, VCF format',
                    'tags': ['23andMe', 'genotyping', 'vcf'],
                    'orig_file_hash': new_hash,
                    'creation_date': arrow.get().format(),
                },
            })
