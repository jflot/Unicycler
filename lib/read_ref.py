'''
Classes for reads and references, and related functions.

Author: Ryan Wick
email: rrwick@gmail.com
'''

import random
import gzip
from misc import quit_with_error, print_progress_line, get_nice_header, get_compression_type, \
                 print_section_header

def load_references(fasta_filename, verbosity):
    '''
    This function loads in sequences from a FASTA file and returns a list of Reference objects.
    '''
    references = []
    total_bases = 0
    print_section_header('Loading references', verbosity)

    if get_compression_type(fasta_filename) == 'gz':
        open_func = gzip.open
    else: # plain text
        open_func = open

    if verbosity > 0:
        num_refs = sum(1 for line in open_func(fasta_filename) if line.startswith('>'))
        if not num_refs:
            quit_with_error('There are no references sequences in ' + fasta_filename)
        print_progress_line(0, num_refs)

    fasta_file = open_func(fasta_filename, 'r')
    name = ''
    sequence = ''
    last_progress = 0.0
    for line in fasta_file:
        line = line.strip()
        if not line:
            continue
        if line.startswith('>'): # Header line = start of new contig
            if name:
                references.append(Reference(name, sequence))
                total_bases += len(sequence)
                if verbosity > 0:
                    progress = 100.0 * len(references) / num_refs
                    progress_rounded_down = float(int(progress))
                    if progress_rounded_down > last_progress:
                        print_progress_line(len(references), num_refs, total_bases)
                        last_progress = progress_rounded_down
                name = ''
                sequence = ''
            name = get_nice_header(line[1:])
        else:
            sequence += line
    fasta_file.close()
    if name:
        references.append(Reference(name, sequence))
        total_bases += len(sequence)
        if verbosity > 0:
            print_progress_line(len(references), num_refs, total_bases)
            print('\n')
    return references

def load_long_reads(fastq_filename, verbosity):
    '''
    This function loads in long reads from a FASTQ file and returns a dictionary where key = read
    name and value = Read object. It also returns a list of read names, in the order they are in
    the file.
    '''
    read_dict = {}
    read_names = []
    total_bases = 0
    last_progress = 0.0

    if get_compression_type(fastq_filename) == 'gz':
        open_func = gzip.open
    else: # plain text
        open_func = open

    print_section_header('Loading reads', verbosity)
    if verbosity > 0:
        num_reads = sum(1 for line in open_func(fastq_filename)) // 4
        print_progress_line(0, num_reads)
    fastq = open_func(fastq_filename, 'r')
    for line in fastq:
        name = line.strip()[1:].split()[0]
        sequence = next(fastq).strip()
        _ = next(fastq)
        qualities = next(fastq).strip()
        read_dict[name] = Read(name, sequence, qualities)
        read_names.append(name)
        total_bases += len(sequence)
        if verbosity > 0:
            progress = 100.0 * len(read_dict) / num_reads
            progress_rounded_down = float(int(progress))
            if progress_rounded_down > last_progress:
                print_progress_line(len(read_dict), num_reads, total_bases)
                last_progress = progress_rounded_down    
    fastq.close()
    if verbosity > 0:
        print()
    return read_dict, read_names

def simplify_ranges(ranges):
    '''
    Collapses overlapping ranges together. Input ranges are tuples of (start, end) in the normal
    Python manner where the end isn't included.
    '''
    fixed_ranges = []
    for int_range in ranges:
        if int_range[0] > int_range[1]:
            fixed_ranges.append((int_range[1], int_range[0]))
        elif int_range[0] < int_range[1]:
            fixed_ranges.append(int_range)
    starts_ends = [(x[0], 1) for x in fixed_ranges]
    starts_ends += [(x[1], -1) for x in fixed_ranges]
    starts_ends.sort(key=lambda x: x[0])
    current_sum = 0
    cumulative_sum = []
    for start_end in starts_ends:
        current_sum += start_end[1]
        cumulative_sum.append((start_end[0], current_sum))
    prev_depth = 0
    start = 0
    combined = []
    for pos, depth in cumulative_sum:
        if prev_depth == 0:
            start = pos
        elif depth == 0:
            combined.append((start, pos))
        prev_depth = depth
    return combined

def range_is_contained(test_range, other_ranges):
    '''
    Returns True if test_range is entirely contained within any range in other_ranges.
    '''
    start, end = test_range
    for other_range in other_ranges:
        if other_range[0] <= start and other_range[1] >= end:
            return True
    return False

def range_overlap(test_range, other_ranges):
    '''
    Returns the size of the overlap (integer) between the two ranges.
    '''
    start, end = test_range
    max_overlap = 0
    for other_range in other_ranges:
        max_overlap = max(max_overlap, min(end, other_range[1]) - max(start, other_range[0]))
    return max_overlap



class Reference(object):
    '''
    This class holds a reference sequence: just a name and a nucleotide sequence.
    '''
    def __init__(self, name, sequence):
        self.name = name
        self.sequence = sequence.upper()

        # If the reference name also happens to be a number, store it as an int.
        try:
            self.number = int(name)
        except ValueError:
            self.number = 0

    def get_length(self):
        '''
        Returns the sequence length.
        '''
        return len(self.sequence)


class Read(object):
    '''
    This class holds a long read, e.g. from PacBio or Oxford Nanopore.
    '''
    def __init__(self, name, sequence, qualities):
        self.name = name
        self.sequence = sequence.upper()
        self.qualities = qualities
        self.alignments = []

    def __repr__(self):
        return self.name + ' (' + str(len(self.sequence)) + ' bp)'

    def get_length(self):
        '''
        Returns the sequence length.
        '''
        return len(self.sequence)

    def needs_seqan_realignment(self, low_score_threshold):
        '''
        This function returns True or False based on whether a read was nicely aligned by GraphMap
        or needs to be realigned with Seqan.
        '''
        # Either zero or more than one alignments result in realignment.
        if len(self.alignments) != 1:
            return True

        # Overlapping alignments or low quality alignments result in realignment.
        only_alignment = self.alignments[0]
        return (not only_alignment.is_whole_read() or
                only_alignment.scaled_score < low_score_threshold)

    def remove_conflicting_alignments(self, allowed_overlap):
        '''
        This function removes alignments from the read which are likely to be spurious or
        redundant.
        '''
        self.alignments = sorted(self.alignments, reverse=True,
                                 key=lambda x: (x.raw_score, random.random()))
        kept_alignments = []
        kept_alignment_ranges = []
        for alignment in self.alignments:
            this_range = alignment.read_start_end_positive_strand()

            # Don't keep alignments for which their part of the read is already aligned.
            if range_is_contained(this_range, kept_alignment_ranges):
                continue

            # Don't keep alignments which overlap too much with existing alignments.
            if range_overlap(this_range, kept_alignment_ranges) > allowed_overlap:
                continue

            # Don't keep alignments that seem to be very similar to an already kept alignment.
            keep_alignment = True
            for kept_alignment in kept_alignments:
                if kept_alignment.is_very_similar(alignment):
                    keep_alignment = False
                    break

            if keep_alignment:
                kept_alignments.append(alignment)
                kept_alignment_ranges = simplify_ranges(kept_alignment_ranges + [this_range])

        kept_alignments = sorted(kept_alignments,
                                 key=lambda x: x.read_start_end_positive_strand()[0])
        self.alignments = kept_alignments

    def remove_low_score_alignments(self, low_score_threshold):
        '''
        This function removes alignments with identity below the cutoff.
        '''
        self.alignments = [x for x in self.alignments if x.scaled_score >= low_score_threshold]

    def remove_short_alignments(self, min_align_length):
        '''
        This function removes alignments with identity below the cutoff.
        '''
        self.alignments = [x for x in self.alignments \
                           if x.get_aligned_ref_length() >= min_align_length]

    def get_fastq(self):
        '''
        Returns a string for the read in FASTQ format. It contains four lines and ends in a line
        break.
        '''
        return '@' + self.name + '\n' + \
               self.sequence + '\n' + \
               '+' + self.name + '\n' + \
               self.qualities + '\n'

    def get_fasta(self):
        '''
        Returns a string for the read in FASTA format. It contains two lines and ends in a line
        break.
        '''
        return '>' + self.name + '\n' + \
               self.sequence + '\n'

    def get_descriptive_string(self):
        '''
        Returns a multi-line string that describes the read and its alignments.
        '''
        header = self.name + ' (' + str(len(self.sequence)) + ' bp)'
        line = '-' * len(header)
        description = header + '\n' + line + '\n'
        if not self.alignments:
            description += 'no alignments'
        else:
            description += '%.2f' % (100.0 * self.get_fraction_aligned()) + '% aligned\n'
            description += '\n'.join([str(x) for x in self.alignments])
        return description + '\n\n'

    def get_fraction_aligned(self):
        '''
        This function returns the fraction of the read which is covered by any of the read's
        alignments.
        '''
        read_ranges = [x.read_start_end_positive_strand() \
                       for x in self.alignments]
        read_ranges = simplify_ranges(read_ranges)
        aligned_length = sum([x[1] - x[0] for x in read_ranges])
        return aligned_length / len(self.sequence)

    def get_reference_bases_aligned(self):
        '''
        This function returns the number of bases aligned with respect to the reference.
        '''
        return sum([x.get_aligned_ref_length() for x in self.alignments])

    def has_one_contained_alignment(self):
        '''
        Returns true if this read aligned entirely within a reference (i.e. no read end gaps).
        '''
        return len(self.alignments) == 1 and \
               self.alignments[0].read_start_pos == 0 and \
               self.alignments[0].read_end_gap == 0



