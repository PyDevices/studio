import com.soundhelix.component.player.impl.MidiPlayer;
import com.soundhelix.misc.Arrangement;
import com.soundhelix.misc.SongContext;
import com.soundhelix.misc.Structure;
import com.soundhelix.util.SongUtils;

import javax.sound.midi.MetaMessage;
import javax.sound.midi.MidiEvent;
import javax.sound.midi.MidiSystem;
import javax.sound.midi.ShortMessage;
import java.io.File;
import java.io.FileWriter;
import java.io.Writer;
import java.lang.reflect.Field;

/**
 * Generates a SoundHelix song and writes it out without playing it: a type-1
 * MIDI file (OUT.mid) and a JSON note list (OUT.json) that also carries the
 * player's tempo and groove.
 *
 * SoundHelix's own MidiPlayer writes MIDI only while it plays through a
 * sound device, so a machine without one (WSL, CI) gets nothing from it.
 *
 * Usage: java Export SONG.xml SONG_NAME OUT
 * The song name seeds the generator: the same name gives the same song.
 */
public class Export {
    public static void main(String[] args) throws Exception {
        String xml = args[0], name = args[1], out = args[2];
        SongContext ctx = SongUtils.generateSong(new File(xml).toURI().toURL(), name);
        Structure st = ctx.getStructure();
        MidiPlayer player = (MidiPlayer) ctx.getPlayer();
        // Melodic pitches are relative to the player's transposition; the groove
        // is the player's swing. Neither has a getter.
        int base = field(player, "transposition", Integer.class);
        int[] groove = field(player, "groove", int[].class);
        int tpb = st.getTicksPerBeat(), maxVelocity = st.getMaxVelocity();

        javax.sound.midi.Sequence midi = new javax.sound.midi.Sequence(javax.sound.midi.Sequence.PPQ, tpb);
        javax.sound.midi.Track tempo = midi.createTrack();
        int usPerBeat = (int) (60000000000L / player.getMilliBPM());
        tempo.add(new MidiEvent(new MetaMessage(0x51, new byte[] {
            (byte) (usPerBeat >> 16), (byte) (usPerBeat >> 8), (byte) usPerBeat}, 3), 0));

        StringBuilder js = new StringBuilder();
        js.append("{\"name\":\"").append(name).append("\",\"milli_bpm\":").append(player.getMilliBPM())
          .append(",\"ticks_per_beat\":").append(tpb).append(",\"ticks_per_bar\":").append(st.getTicksPerBar())
          .append(",\"bars\":").append(st.getBars()).append(",\"groove\":[");
        for (int i = 0; i < groove.length; i++) {
            js.append(i > 0 ? "," : "").append(groove[i]);
        }
        js.append("],\"tracks\":[");

        int nextChannel = 0, count = 0;
        for (Arrangement.ArrangementEntry entry : ctx.getArrangement()) {
            com.soundhelix.misc.Track track = entry.getTrack();
            boolean drums = track.getType() == com.soundhelix.misc.Track.TrackType.RHYTHMIC;
            int channel = 9;
            if (!drums) {
                if (nextChannel == 9) {
                    nextChannel++;
                }
                channel = nextChannel++;
            }
            String instrument = entry.getInstrument();
            javax.sound.midi.Track mt = midi.createTrack();
            mt.add(new MidiEvent(new MetaMessage(0x03, instrument.getBytes(), instrument.length()), 0));
            js.append(count++ > 0 ? "," : "").append("{\"instrument\":\"").append(instrument)
              .append("\",\"drums\":").append(drums).append(",\"notes\":[");
            int notes = 0;
            for (int s = 0; s < track.size(); s++) {
                int tick = 0;
                for (com.soundhelix.misc.Sequence.SequenceEntry e : track.get(s)) {
                    if (e.isNote()) {
                        int pitch = e.getPitch() + (drums ? 0 : base);
                        int velocity = Math.max(1, Math.min(127, e.getVelocity() * 127 / maxVelocity));
                        mt.add(new MidiEvent(new ShortMessage(ShortMessage.NOTE_ON, channel, pitch, velocity), tick));
                        mt.add(new MidiEvent(new ShortMessage(ShortMessage.NOTE_OFF, channel, pitch, 0),
                                             tick + e.getTicks()));
                        // [start, length, pitch, velocity], in SoundHelix ticks
                        js.append(notes++ > 0 ? "," : "").append("[").append(tick).append(",")
                          .append(e.getTicks()).append(",").append(pitch).append(",").append(velocity).append("]");
                    }
                    tick += e.getTicks();
                }
            }
            js.append("]}");
        }
        js.append("]}");

        MidiSystem.write(midi, 1, new File(out + ".mid"));
        try (Writer w = new FileWriter(out + ".json")) {
            w.write(js.toString());
        }
        System.out.printf("%s: %.1f BPM, %d bars, groove %s%n", name, player.getMilliBPM() / 1000.0,
                          st.getBars(), java.util.Arrays.toString(groove));
    }

    @SuppressWarnings("unchecked")
    private static <T> T field(Object obj, String name, Class<T> type) throws Exception {
        Field f = obj.getClass().getDeclaredField(name);
        f.setAccessible(true);
        return (T) f.get(obj);
    }
}
